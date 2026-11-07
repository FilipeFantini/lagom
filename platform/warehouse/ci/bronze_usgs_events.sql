-- A small stand-in for bronze.usgs_events, for the dbt build in CI.
-- Same columns and types as dlt writes them (only the ones silver reads).
-- Hand-made rows, no real data. What they exercise:
--   ev_a    two revisions: silver must keep the later one (automatic -> reviewed)
--   ev_b..  one event per magnitude class and per depth class
--   ev_blast  a quarry blast: in silver, not in gold
--   ev_upper  status in capitals, as network pt sends it
--   ev_d    'Fiji region', a place with no comma: the region is the whole place
--   ev_late   after midnight UTC: a second day in the daily aggregate
create schema if not exists bronze;

create or replace table bronze.usgs_events (
    id varchar,
    properties__type varchar,
    properties__mag double,
    properties__mag_type varchar,
    properties__place varchar,
    properties__time bigint,
    properties__updated bigint,
    geometry__coordinates json,
    properties__status varchar,
    properties__tsunami bigint,
    properties__sig bigint,
    properties__felt bigint,
    properties__alert varchar,
    properties__net varchar,
    properties__url varchar,
    properties__title varchar
);

insert into bronze.usgs_events values
    ('ev_a', 'earthquake', 1.5, 'ml', '10 km N of Town, Alaska', 1760000000000, 1760000100000,
        '[-150.0, 61.0, 10.0]', 'automatic', 0, 35, null, null, 'ak', 'https://example.test/ev_a', 'M 1.5 - Alaska'),
    ('ev_a', 'earthquake', 1.7, 'ml', '10 km N of Town, Alaska', 1760000000000, 1760000900000,
        '[-150.0, 61.0, 10.0]', 'reviewed', 0, 40, 2, null, 'ak', 'https://example.test/ev_a', 'M 1.7 - Alaska'),
    ('ev_b', 'earthquake', 3.0, 'ml', '5 km S of Place, CA', 1760001000000, 1760001000000,
        '[-120.5, 36.0, 5.5]', 'reviewed', 0, 150, 10, 'green', 'ci', 'https://example.test/ev_b', 'M 3.0 - CA'),
    ('ev_c', 'earthquake', 4.5, 'mb', '20 km E of Place, Nevada', 1760002000000, 1760002000000,
        '[-117.0, 39.0, 80.0]', 'reviewed', 0, 300, 0, null, 'nn', 'https://example.test/ev_c', 'M 4.5 - Nevada'),
    ('ev_d', 'earthquake', 5.5, 'mww', 'Fiji region', 1760003000000, 1760003000000,
        '[178.0, -18.0, 350.0]', 'reviewed', 0, 465, 0, 'yellow', 'us', 'https://example.test/ev_d', 'M 5.5 - Fiji region'),
    ('ev_e', 'earthquake', 6.5, 'mww', '100 km W of Coast, Chile', 1760004000000, 1760004000000,
        '[-72.0, -33.0, 25.0]', 'reviewed', 1, 720, 5, 'orange', 'us', 'https://example.test/ev_e', 'M 6.5 - Chile'),
    ('ev_f', 'earthquake', 7.5, 'mww', '50 km S of Coast, Japan', 1760005000000, 1760005000000,
        '[142.0, 38.0, 30.0]', 'reviewed', 1, 1000, 100, 'red', 'us', 'https://example.test/ev_f', 'M 7.5 - Japan'),
    ('ev_g', 'earthquake', 8.2, 'mww', '200 km E of Coast, Japan', 1760006000000, 1760006000000,
        '[144.0, 38.0, 20.0]', 'reviewed', 1, 1100, 200, 'red', 'us', 'https://example.test/ev_g', 'M 8.2 - Japan'),
    ('ev_h', 'earthquake', 0.5, 'ml', '3 km N of Town, Alaska', 1760007000000, 1760007000000,
        '[-150.1, 61.1, 2.0]', 'automatic', 0, 5, null, null, 'ak', 'https://example.test/ev_h', 'M 0.5 - Alaska'),
    ('ev_blast', 'quarry blast', 1.0, 'ml', '1 km N of Quarry, Texas', 1760008000000, 1760008000000,
        '[-98.0, 30.0, 0.0]', 'reviewed', 0, 10, null, null, 'tx', 'https://example.test/ev_blast', 'M 1.0 - Texas'),
    ('ev_upper', 'earthquake', 2.5, 'ml', '8 km W of Town, Portugal', 1760009000000, 1760009000000,
        '[-9.0, 38.0, 15.0]', 'REVIEWED', 0, 90, null, null, 'pt', 'https://example.test/ev_upper', 'M 2.5 - Portugal'),
    ('ev_late', 'earthquake', 4.0, 'mb', '7 km S of Town, Alaska', 1760054400000, 1760054400000,
        '[-150.2, 61.2, 12.0]', 'automatic', 0, 250, 1, null, 'ak', 'https://example.test/ev_late', 'M 4.0 - Alaska');
