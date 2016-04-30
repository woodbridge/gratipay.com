CREATE TABLE countries -- http://www.iso.org/iso/country_codes
( id    bigserial   primary key
, code2 text        NOT NULL UNIQUE
, code3 text        NOT NULL UNIQUE
, name  text        NOT NULL UNIQUE
 );

\i sql/countries.sql

CREATE TABLE participant_identities
( id                bigserial   primary key
, participant_id    bigint      NOT NULL REFERENCES participants(id)
, country_id        bigint      NOT NULL REFERENCES countries(id)
, schema_name       text        NOT NULL
, info              bytea       NOT NULL
, UNIQUE(participant_id, country_id)
 );
