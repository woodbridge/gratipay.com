from __future__ import print_function, unicode_literals

import datetime
import os
import random

import mock
import pytest

from aspen.utils import utcnow
from gratipay import NotSane
from gratipay.billing.instruments import CreditCard
from gratipay.exceptions import (
    UsernameIsEmpty,
    UsernameTooLong,
    UsernameAlreadyTaken,
    UsernameContainsInvalidCharacters,
    UsernameIsRestricted,
    BadAmount,
)
from gratipay.models.account_elsewhere import AccountElsewhere
from gratipay.models.exchange_route import ExchangeRoute
from gratipay.models.participant import (
    LastElsewhere, NeedConfirmation, NonexistingElsewhere, Participant, TeamCantBeOnlyAuth,
    WontTakeOverWithIdentities
)
from gratipay.testing import Harness, D,P,T


# TODO: Test that accounts elsewhere are not considered claimed by default


class TestNeedConfirmation(Harness):
    def test_need_confirmation1(self):
        assert not NeedConfirmation(False, False, False)

    def test_need_confirmation2(self):
        assert NeedConfirmation(False, False, True)

    def test_need_confirmation3(self):
        assert not NeedConfirmation(False, True, False)

    def test_need_confirmation4(self):
        assert NeedConfirmation(False, True, True)

    def test_need_confirmation5(self):
        assert NeedConfirmation(True, False, False)

    def test_need_confirmation6(self):
        assert NeedConfirmation(True, False, True)

    def test_need_confirmation7(self):
        assert NeedConfirmation(True, True, False)

    def test_need_confirmation8(self):
        assert NeedConfirmation(True, True, True)


class TestAbsorptions(Harness):
    # TODO: These tests should probably be moved to absorptions tests
    def setUp(self):
        Harness.setUp(self)
        now = utcnow()
        hour_ago = now - datetime.timedelta(hours=1)
        for i, username in enumerate(['alice', 'bob', 'carl']):
            p = self.make_participant( username
                                     , claimed_time=hour_ago
                                     , last_bill_result=''
                                     , balance=D(i)
                                      )
            setattr(self, username, p)

        deadbeef = self.make_participant('deadbeef', balance=D('18.03'), elsewhere='twitter')
        self.expected_new_balance = self.bob.balance + deadbeef.balance
        deadbeef_twitter = AccountElsewhere.from_user_name('twitter', 'deadbeef')

        self.make_tip(self.carl, self.bob, '1.00')
        self.make_tip(self.alice, deadbeef, '1.00')
        self.bob.take_over(deadbeef_twitter, have_confirmation=True)
        self.deadbeef_archived = Participant.from_id(deadbeef.id)

    def test_participant_can_be_instantiated(self):
        expected = Participant
        actual = P('alice').__class__
        assert actual is expected

    @pytest.mark.xfail(reason="#3399")
    def test_bob_has_two_dollars_in_tips(self):
        expected = D('2.00')
        actual = self.bob.receiving
        assert actual == expected

    def test_alice_gives_to_bob_now(self):
        assert self.get_tip('alice', 'bob') == D('1.00')

    def test_deadbeef_is_archived(self):
        actual = self.db.one( "SELECT count(*) FROM absorptions "
                              "WHERE absorbed_by='bob' AND absorbed_was='deadbeef'"
                             )
        expected = 1
        assert actual == expected

    def test_alice_doesnt_gives_to_deadbeef_anymore(self):
        assert self.get_tip('alice', 'deadbeef') == D('0.00')

    def test_alice_doesnt_give_to_whatever_deadbeef_was_archived_as_either(self):
        assert self.get_tip('alice', self.deadbeef_archived.username) == D('0.00')

    def test_there_is_no_more_deadbeef(self):
        actual = P('deadbeef')
        assert actual is None

    def test_balance_was_transferred(self):
        fresh_bob = P('bob')
        assert fresh_bob.balance == self.bob.balance == self.expected_new_balance
        assert self.deadbeef_archived.balance == 0


class TestTakeOver(Harness):

    def test_cross_tip_doesnt_become_self_tip(self):
        alice_twitter = self.make_elsewhere('twitter', 1, 'alice')
        bob_twitter   = self.make_elsewhere('twitter', 2, 'bob')
        alice = alice_twitter.opt_in('alice')[0].participant
        bob = bob_twitter.opt_in('bob')[0].participant
        self.make_tip(alice, bob, '1.00')
        bob.take_over(alice_twitter, have_confirmation=True)
        self.db.self_check()

    def test_zero_cross_tip_doesnt_become_self_tip(self):
        alice_twitter = self.make_elsewhere('twitter', 1, 'alice')
        bob_twitter   = self.make_elsewhere('twitter', 2, 'bob')
        alice = alice_twitter.opt_in('alice')[0].participant
        bob = bob_twitter.opt_in('bob')[0].participant
        self.make_tip(alice, bob, '1.00')
        self.make_tip(alice, bob, '0.00')

        bob.take_over(alice_twitter, have_confirmation=True)
        self.db.self_check()

    def test_do_not_take_over_zero_tips_giving(self):
        alice_twitter = self.make_elsewhere('twitter', 1, 'alice')
        bob = self.make_elsewhere('twitter', 2, 'bob').opt_in('bob')[0].participant
        carl_twitter  = self.make_elsewhere('twitter', 3, 'carl')
        alice = alice_twitter.opt_in('alice')[0].participant
        carl = carl_twitter.opt_in('carl')[0].participant
        self.make_tip(carl, bob, '1.00')
        self.make_tip(carl, bob, '0.00')
        alice.take_over(carl_twitter, have_confirmation=True)
        ntips = self.db.one("select count(*) from tips")
        assert 2 == ntips
        self.db.self_check()

    def test_do_not_take_over_zero_tips_receiving(self):
        alice_twitter = self.make_elsewhere('twitter', 1, 'alice')
        bob_twitter   = self.make_elsewhere('twitter', 2, 'bob')
        carl_twitter  = self.make_elsewhere('twitter', 3, 'carl')
        alice = alice_twitter.opt_in('alice')[0].participant
        bob = bob_twitter.opt_in('bob')[0].participant
        carl = carl_twitter.opt_in('carl')[0].participant
        self.make_tip(bob, carl, '1.00')
        self.make_tip(bob, carl, '0.00')
        alice.take_over(carl_twitter, have_confirmation=True)
        ntips = self.db.one("select count(*) from tips")
        assert 2 == ntips
        self.db.self_check()

    def test_is_funded_is_correct_for_consolidated_tips_receiving(self):
        alice = self.make_participant('alice', claimed_time='now', balance=1)
        bob = self.make_participant('bob', elsewhere='twitter')
        carl = self.make_participant('carl', elsewhere='github')
        self.make_tip(alice, bob, '1.00')  # funded
        self.make_tip(alice, carl, '5.00')  # not funded
        bob.take_over(('github', str(carl.id)), have_confirmation=True)
        tips = self.db.all("select * from tips where amount > 0 order by id asc")
        assert len(tips) == 3
        assert tips[-1].amount == 6
        assert tips[-1].is_funded is False
        self.db.self_check()

    def test_take_over_fails_if_it_would_result_in_just_a_team_account(self):
        alice_github = self.make_elsewhere('github', 2, 'alice')
        alice = alice_github.opt_in('alice')[0].participant

        a_team_github = self.make_elsewhere('github', 1, 'a_team', is_team=True)
        a_team_github.opt_in('a_team')

        pytest.raises( TeamCantBeOnlyAuth
                     , alice.take_over
                     , a_team_github
                     , have_confirmation=True
                      )

    def test_take_over_is_fine_with_identity_info_on_primary(self):
        TT = self.db.one("SELECT id FROM countries WHERE code='TT'")
        alice = self.make_participant('alice')
        alice.add_email('alice@example.com')
        alice.verify_email('alice@example.com', alice.get_email('alice@example.com').nonce)
        alice.store_identity_info(TT, 'nothing-enforced', {})

        bob_github = self.make_elsewhere('github', 2, 'bob')
        bob_github.opt_in('bob')

        alice.take_over(bob_github, have_confirmation=True)
        self.db.self_check()

    def test_take_over_fails_if_secondary_has_identity_info(self):
        TT = self.db.one("SELECT id FROM countries WHERE code='TT'")
        alice = self.make_participant('alice')

        bob_github = self.make_elsewhere('github', 2, 'bob')
        bob = bob_github.opt_in('bob')[0].participant
        bob.add_email('bob@example.com')
        bob.verify_email('bob@example.com', bob.get_email('bob@example.com').nonce)
        bob.store_identity_info(TT, 'nothing-enforced', {})

        pytest.raises(WontTakeOverWithIdentities, alice.take_over, bob_github)

    def test_idempotent(self):
        alice_twitter = self.make_elsewhere('twitter', 1, 'alice')
        bob_github    = self.make_elsewhere('github', 2, 'bob')
        alice = alice_twitter.opt_in('alice')[0].participant
        alice.take_over(bob_github, have_confirmation=True)
        alice.take_over(bob_github, have_confirmation=True)
        self.db.self_check()

    @mock.patch.object(Participant, '_mailer')
    def test_email_addresses_merging(self, mailer):
        alice = self.make_participant('alice')
        alice.add_email('alice@example.com')
        alice.add_email('alice@example.net')
        alice.add_email('alice@example.org')
        alice.verify_email('alice@example.org', alice.get_email('alice@example.org').nonce)
        bob_github = self.make_elsewhere('github', 2, 'bob')
        bob = bob_github.opt_in('bob')[0].participant
        bob.add_email('alice@example.com', '0 seconds')
        bob.verify_email('alice@example.com', bob.get_email('alice@example.com').nonce)
        bob.add_email('alice@example.net', '0 seconds')
        bob.add_email('bob@example.net', '0 seconds')
        alice.take_over(bob_github, have_confirmation=True)

        alice_emails = {e.address: e for e in alice.get_emails()}
        assert len(alice_emails) == 4
        assert alice_emails['alice@example.com'].verified
        assert alice_emails['alice@example.org'].verified
        assert not alice_emails['alice@example.net'].verified
        assert not alice_emails['bob@example.net'].verified

        assert not Participant.from_id(bob.id).get_emails()


    # The below tests were moved up here from TestParticipant, and may be duplicates.

    def hackedSetUp(self):
        for username in ['alice', 'bob', 'carl']:
            p = self.make_participant(username, claimed_time='now', elsewhere='twitter')
            setattr(self, username, p)

    def test_connecting_unknown_account_fails(self):
        self.hackedSetUp()
        with self.assertRaises(NotSane):
            self.bob.take_over(('github', 'jim'))

    def test_cant_take_over_claimed_participant_without_confirmation(self):
        self.hackedSetUp()
        with self.assertRaises(NeedConfirmation):
            self.alice.take_over(('twitter', str(self.bob.id)))

    def test_taking_over_yourself_sets_all_to_zero(self):
        self.hackedSetUp()
        self.make_tip(self.alice, self.bob, '1.00')
        self.alice.take_over(('twitter', str(self.bob.id)), have_confirmation=True)
        expected = D('0.00')
        actual = self.alice.giving
        assert actual == expected

    def test_alice_ends_up_tipping_bob_two_dollars(self):
        self.hackedSetUp()
        self.make_tip(self.alice, self.bob, '1.00')
        self.make_tip(self.alice, self.carl, '1.00')
        self.bob.take_over(('twitter', str(self.carl.id)), have_confirmation=True)
        assert self.get_tip('alice', 'bob') == D('2.00')

    def test_bob_ends_up_tipping_alice_two_dollars(self):
        self.hackedSetUp()
        self.make_tip(self.bob, self.alice, '1.00')
        self.make_tip(self.carl, self.alice, '1.00')
        self.bob.take_over(('twitter', str(self.carl.id)), have_confirmation=True)
        assert self.get_tip('bob', 'alice') == D('2.00')

    def test_ctime_comes_from_the_older_tip(self):
        self.hackedSetUp()
        self.make_tip(self.alice, self.bob, '1.00')
        self.make_tip(self.alice, self.carl, '1.00')
        self.bob.take_over(('twitter', str(self.carl.id)), have_confirmation=True)

        ctimes = self.db.all("""
            SELECT ctime
              FROM tips
             WHERE tipper = 'alice'
               AND tippee = 'bob'
        """)
        assert len(ctimes) == 2
        assert ctimes[0] == ctimes[1]


class TestParticipant(Harness):
    def setUp(self):
        Harness.setUp(self)
        for username in ['alice', 'bob', 'carl']:
            p = self.make_participant(username, claimed_time='now', elsewhere='twitter')
            setattr(self, username, p)

    def test_comparison(self):
        assert self.alice == self.alice
        assert not (self.alice != self.alice)
        assert self.alice != self.bob
        assert not (self.alice == self.bob)
        assert self.alice != None
        assert not (self.alice == None)

    def test_delete_elsewhere_last(self):
        with pytest.raises(LastElsewhere):
            self.alice.delete_elsewhere('twitter', self.alice.id)

    def test_delete_elsewhere_last_signin(self):
        self.make_elsewhere('bountysource', self.alice.id, 'alice')
        with pytest.raises(LastElsewhere):
            self.alice.delete_elsewhere('twitter', self.alice.id)

    def test_delete_elsewhere_nonsignin(self):
        g = self.make_elsewhere('bountysource', 1, 'alice')
        alice = self.alice
        alice.take_over(g)
        accounts = alice.get_accounts_elsewhere()
        assert accounts['twitter'] and accounts['bountysource']
        alice.delete_elsewhere('bountysource', 1)
        accounts = alice.get_accounts_elsewhere()
        assert accounts['twitter'] and accounts.get('bountysource') is None

    def test_delete_elsewhere_nonexisting(self):
        with pytest.raises(NonexistingElsewhere):
            self.alice.delete_elsewhere('github', 1)

    def test_delete_elsewhere(self):
        g = self.make_elsewhere('github', 1, 'alice')
        alice = self.alice
        alice.take_over(g)
        # test preconditions
        accounts = alice.get_accounts_elsewhere()
        assert accounts['twitter'] and accounts['github']
        # do the thing
        alice.delete_elsewhere('twitter', alice.id)
        # unit test
        accounts = alice.get_accounts_elsewhere()
        assert accounts.get('twitter') is None and accounts['github']


class Tests(Harness):

    def random_restricted_username(self):
        """Helper method to chooses a restricted username for testing"""
        from gratipay import RESTRICTED_USERNAMES
        random_item = random.choice(RESTRICTED_USERNAMES)
        while any(map(random_item.startswith, ('%', '~'))):
            random_item = random.choice(RESTRICTED_USERNAMES)
        return random_item

    def setUp(self):
        Harness.setUp(self)
        self.participant = self.make_participant('user1')  # Our protagonist


    def test_claiming_participant(self):
        now = utcnow()
        self.participant.set_as_claimed()
        actual = self.participant.claimed_time - now
        expected = datetime.timedelta(seconds=0.1)
        assert actual < expected

    def test_changing_username_successfully(self):
        self.participant.change_username('user2')
        actual = P('user2')
        assert self.participant == actual

    def test_changing_username_to_nothing(self):
        with self.assertRaises(UsernameIsEmpty):
            self.participant.change_username('')

    def test_changing_username_to_all_spaces(self):
        with self.assertRaises(UsernameIsEmpty):
            self.participant.change_username('    ')

    def test_changing_username_strips_spaces(self):
        self.participant.change_username('  aaa  ')
        actual = P('aaa')
        assert self.participant == actual

    def test_changing_username_returns_the_new_username(self):
        returned = self.participant.change_username('  foo bar baz  ')
        assert returned == 'foo bar baz', returned

    def test_changing_username_to_too_long(self):
        with self.assertRaises(UsernameTooLong):
            self.participant.change_username('123456789012345678901234567890123')

    def test_changing_username_to_already_taken(self):
        self.make_participant('user2')
        with self.assertRaises(UsernameAlreadyTaken):
            self.participant.change_username('user2')

    def test_changing_username_to_already_taken_is_case_insensitive(self):
        self.make_participant('UsEr2')
        with self.assertRaises(UsernameAlreadyTaken):
            self.participant.change_username('uSeR2')

    def test_changing_username_to_invalid_characters(self):
        with self.assertRaises(UsernameContainsInvalidCharacters):
            self.participant.change_username(u"\u2603") # Snowman

    def test_changing_username_to_restricted_name(self):
        username = self.random_restricted_username()
        with self.assertRaises(UsernameIsRestricted):
            self.participant.change_username(username)
        assert os.path.exists(self.client.www_root + '/' + username)

    # id

    def test_participant_gets_a_long_id(self):
        actual = type(self.make_participant('alice').id)
        assert actual == long


    # set_payment_instruction - spi

    def test_spi_sets_payment_instruction(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        alice.set_payment_instruction(team, '1.00')

        actual = alice.get_payment_instruction(team)['amount']
        assert actual == D('1.00')

    def test_spi_returns_a_dict(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        actual = alice.set_payment_instruction(team, '1.00')
        assert isinstance(actual, dict)
        assert isinstance(actual['amount'], D)
        assert actual['amount'] == 1

    def test_spi_allows_up_to_a_thousand(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        alice.set_payment_instruction(team, '1000.00')

    def test_spi_doesnt_allow_a_penny_more(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        self.assertRaises(BadAmount, alice.set_payment_instruction, team, '1000.01')

    def test_spi_allows_a_zero_payment_instruction(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        alice.set_payment_instruction(team, '0.00')

    def test_spi_doesnt_allow_a_penny_less(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        self.assertRaises(BadAmount, alice.set_payment_instruction, team, '-0.01')

    def test_spi_is_free_rider_defaults_to_none(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        assert alice.is_free_rider is None

    def test_spi_sets_is_free_rider_to_false(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        gratipay = self.make_team('Gratipay', owner=self.make_participant('Gratipay').username)
        alice.set_payment_instruction(gratipay, '0.01')
        assert alice.is_free_rider is False
        assert P('alice').is_free_rider is False

    def test_spi_resets_is_free_rider_to_null(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        gratipay = self.make_team('Gratipay', owner=self.make_participant('Gratipay').username)
        alice.set_payment_instruction(gratipay, '0.00')
        assert alice.is_free_rider is None
        assert P('alice').is_free_rider is None

    def test_spi_sets_id_fields(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team()
        actual = alice.set_payment_instruction(team, '1.00')
        assert actual['participant_id'] == alice.id
        assert actual['team_id'] == team.id


    # get_teams - gt

    def test_get_teams_gets_teams(self):
        self.make_team(is_approved=True)
        picard = P('picard')
        assert [t.slug for t in picard.get_teams()] == ['TheEnterprise']

    def test_get_teams_can_get_only_approved_teams(self):
        self.make_team(is_approved=True)
        picard = P('picard')
        self.make_team('The Stargazer', owner=picard, is_approved=False)
        assert [t.slug for t in picard.get_teams(only_approved=True)] == ['TheEnterprise']

    def test_get_teams_can_get_all_teams(self):
        self.make_team(is_approved=True)
        picard = P('picard')
        self.make_team('The Stargazer', owner=picard, is_approved=False)
        assert [t.slug for t in picard.get_teams()] == ['TheEnterprise', 'TheStargazer']


    # giving

    def test_giving_only_includes_funded_payment_instructions(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        bob = self.make_participant('bob', claimed_time='now')
        carl = self.make_participant('carl', claimed_time='now', last_bill_result="Fail!")
        team = self.make_team(is_approved=True)

        alice.set_payment_instruction(team, '3.00') # The only funded tip
        bob.set_payment_instruction(team, '5.00')
        carl.set_payment_instruction(team, '7.00')

        assert alice.giving == D('3.00')
        assert bob.giving == D('0.00')
        assert carl.giving == D('0.00')

        funded_tip = self.db.one("SELECT * FROM payment_instructions WHERE is_funded ORDER BY id")
        assert funded_tip.participant_id == alice.id

    def test_giving_only_includes_the_latest_payment_instruction(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team(is_approved=True)

        alice.set_payment_instruction(team, '12.00')
        alice.set_payment_instruction(team, '4.00')

        assert alice.giving == D('4.00')

    @mock.patch('braintree.PaymentMethod.delete')
    def test_giving_is_updated_when_credit_card_is_updated(self, btd):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='fail')
        team = self.make_team(is_approved=True)

        alice.set_payment_instruction(team, '5.00') # Not funded, failing card

        assert alice.giving == D('0.00')
        assert T(team.slug).receiving == D('0.00')

        # Alice updates her card..
        ExchangeRoute.from_network(alice, 'braintree-cc').invalidate()
        ExchangeRoute.insert(alice, 'braintree-cc', '/cards/bar', '')

        assert alice.giving == D('5.00')
        assert T(team.slug).receiving == D('5.00')

    @mock.patch('braintree.PaymentMethod.delete')
    def test_giving_is_updated_when_credit_card_fails(self, btd):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team(is_approved=True)

        alice.set_payment_instruction(team, '5.00') # funded

        assert alice.giving == D('5.00')
        assert T(team.slug).receiving == D('5.00')
        assert P(team.owner).taking == D('5.00')

        ExchangeRoute.from_network(alice, 'braintree-cc').update_error("Card expired")

        assert P('alice').giving == D('0.00')
        assert T(team.slug).receiving == D('0.00')
        assert P(team.owner).taking == D('0.00')


    # credit_card_expiring

    def test_credit_card_expiring_no_card(self):
        alice = self.make_participant('alice', claimed_time='now')
        assert alice.credit_card_expiring() == None

    @mock.patch.object(CreditCard, "from_route")
    def test_credit_card_expiring_valid_card(self, cc):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        cc.return_value = CreditCard(
            expiration_year=2050,
            expiration_month=12
        )

        assert alice.credit_card_expiring() == False

    @mock.patch.object(CreditCard, "from_route")
    def test_credit_card_expiring_expired_card(self, cc):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        cc.return_value = CreditCard(
            expiration_year=2010,
            expiration_month=12
        )

        assert alice.credit_card_expiring() == True


    # dues

    def test_dues_are_cancelled_along_with_payment_instruction(self):
        alice = self.make_participant('alice', claimed_time='now', last_bill_result='')
        team = self.make_team(is_approved=True)

        alice.set_payment_instruction(team, '5.00')

        # Fake dues
        self.db.run("""

            UPDATE payment_instructions ppi
               SET due = '5.00'
             WHERE ppi.participant_id = %s
               AND ppi.team_id = %s

        """, (alice.id, team.id, ))

        assert alice.get_due(team) == D('5.00')

        # Increase subscription amount
        alice.set_payment_instruction(team, '10.00')
        assert alice.get_due(team) == D('5.00')

        # Cancel the subscription
        alice.set_payment_instruction(team, '0.00')
        assert alice.get_due(team) == D('0.00')

        # Revive the subscription
        alice.set_payment_instruction(team, '5.00')
        assert alice.get_due(team) == D('0.00')


    # get_age_in_seconds - gais

    def test_gais_gets_age_in_seconds(self):
        alice = self.make_participant('alice', claimed_time='now')
        actual = alice.get_age_in_seconds()
        assert 0 < actual < 1

    def test_gais_returns_negative_one_if_None(self):
        alice = self.make_participant('alice', claimed_time=None)
        actual = alice.get_age_in_seconds()
        assert actual == -1


    # resolve_unclaimed - ru

    def test_ru_returns_None_for_orphaned_participant(self):
        resolved = self.make_participant('alice').resolve_unclaimed()
        assert resolved is None, resolved

    def test_ru_returns_bitbucket_url_for_stub_from_bitbucket(self):
        unclaimed = self.make_elsewhere('bitbucket', '1234', 'alice')
        stub = P(unclaimed.participant.username)
        actual = stub.resolve_unclaimed()
        assert actual == "/on/bitbucket/alice/"

    def test_ru_returns_github_url_for_stub_from_github(self):
        unclaimed = self.make_elsewhere('github', '1234', 'alice')
        stub = P(unclaimed.participant.username)
        actual = stub.resolve_unclaimed()
        assert actual == "/on/github/alice/"

    def test_ru_returns_twitter_url_for_stub_from_twitter(self):
        unclaimed = self.make_elsewhere('twitter', '1234', 'alice')
        stub = P(unclaimed.participant.username)
        actual = stub.resolve_unclaimed()
        assert actual == "/on/twitter/alice/"

    def test_ru_returns_openstreetmap_url_for_stub_from_openstreetmap(self):
        unclaimed = self.make_elsewhere('openstreetmap', '1', 'alice')
        stub = P(unclaimed.participant.username)
        actual = stub.resolve_unclaimed()
        assert actual == "/on/openstreetmap/alice/"


    # archive

    def test_archive_fails_for_team_owner(self):
        alice = self.make_participant('alice')
        self.make_team(owner=alice)
        with self.db.get_cursor() as cursor:
            pytest.raises(alice.StillOnATeam, alice.archive, cursor)

    def test_archive_fails_if_balance_is_positive(self):
        alice = self.make_participant('alice', balance=2)
        with self.db.get_cursor() as cursor:
            pytest.raises(alice.BalanceIsNotZero, alice.archive, cursor)

    def test_archive_fails_if_balance_is_negative(self):
        alice = self.make_participant('alice', balance=-2)
        with self.db.get_cursor() as cursor:
            pytest.raises(alice.BalanceIsNotZero, alice.archive, cursor)

    def test_archive_clears_claimed_time(self):
        alice = self.make_participant('alice')
        with self.db.get_cursor() as cursor:
            archived_as = alice.archive(cursor)
        assert P(archived_as).claimed_time is None

    def test_archive_records_an_event(self):
        alice = self.make_participant('alice')
        with self.db.get_cursor() as cursor:
            archived_as = alice.archive(cursor)
        payload = self.db.one("SELECT * FROM events WHERE payload->>'action' = 'archive'").payload
        assert payload['values']['old_username'] == 'alice'
        assert payload['values']['new_username'] == archived_as


    # suggested_payment

    def test_suggested_payment_is_zero_for_new_user(self):
        alice = self.make_participant('alice')
        assert alice.suggested_payment == 0


    # mo - member_of

    def test_mo_indicates_membership(self):
        enterprise = self.make_team(available=50)
        alice = self.make_participant( 'alice'
                                     , email_address='alice@example.com'
                                     , verified_in='TT'
                                     , claimed_time='now'
                                      )
        picard = Participant.from_username('picard')
        enterprise.add_member(alice, picard)
        assert alice.member_of(enterprise)

    def test_mo_indicates_non_membership(self):
        enterprise = self.make_team()
        assert not self.make_participant('alice').member_of(enterprise)

    def test_mo_is_false_for_owners(self):
        enterprise = self.make_team()
        assert not Participant.from_username('picard').member_of(enterprise)
