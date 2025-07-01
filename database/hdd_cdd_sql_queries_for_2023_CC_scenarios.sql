---These queries interpolate the load change factors from ReEDS zones to SWITCH load zones, calculate the load changes for each load zone for each climate scenario, based on changes in CDD and HDD from 15 GCMS, and create new load time series that include these changes. Finally new global scenarios are created with thes new load scenarios for each GCM.

--------------------------------------------------------------------------
-- 1. Describe the spatial relationship between ReEDS balancing areas and Switch load zones.

-- Import the fractions calculated in QGIS of the population density of intersecting areas of ReEDS and SWITCH zones divided by the total population in each ReEDS zone.
-- These fractions will be used to allocate the Cooling and Heating degree slopes originally calculated for the ReEDS zones to the SWITCH load zones
CREATE TABLE IF NOT EXISTS switch.reeds_ba_switch_lz_intersections (
    reeds_ba_id INT ,
    switch_lz_id INT ,
    LOAD_AREA VARCHAR,
    reeds_ba_fraction_in_switch_lz_by_pop DOUBLE PRECISION,
    PRIMARY KEY (reeds_ba_id, switch_lz_id)
);

-- 2. upload the reeds_ba_switch_lz_interactions csv file calculated from QGIS based on population density of intersecting region / population density of reeds region. The CSV file is "ReEDS_SWITCH_population_weighting_fractions.csv"
-- creates this table from CSV: switch.reeds_ba_switch_lz_intersections

-- 3. Calculate the Cool and Heat Slopes weighted by population for each SWITCH load zone by joining the cool and heat slopes table with table that converts Reeds areas to SWITCH load zones based on population weighting, by Reeds BA

CREATE TABLE switch.reeds_ba_cool_heat_slopes AS
SELECT a.reeds_ba_id, a.reeds_timeslice_id, a.type_degree_days, a.response_mw_per_delta_dd, b.switch_lz_id, b.load_area, b.reeds_ba_fraction_in_switch_lz_by_pop
FROM public.hddcddslope a
JOIN switch.reeds_ba_switch_lz_intersections b
ON a.reeds_ba_id = b.reeds_ba_id;

-- 4. Sum the population-weighted Cool and Heat Reeds slopes by SWITCH load zones to calculate the SWITCH Cool and Heat slopes for each load zone and timeslice
CREATE TABLE switch.switch_lz_cool_heat_slopes_reeds_timeslice AS
SELECT switch_lz_id, load_area, reeds_timeslice_id, type_degree_days, sum(response_mw_per_delta_dd * reeds_ba_fraction_in_switch_lz_by_pop) as switch_response_mw_per_delta_dd
FROM switch.reeds_ba_cool_heat_slopes
GROUP BY switch_lz_id, load_area, type_degree_days, reeds_timeslice_id
ORDER BY 1,2,3,4;

-- 4b Re-run the original query to create a table that translates ReEDS timeslices to Switch raw_timepoints.

-- This date-wise join is extra-long because of the ReEDS timeslices that go from
-- 10pm to 6am, and because winter goes from October to February (10 to 2).
-- The differences in < and <= for hour_of_day_end and month_end
-- is intentional to accomodate differing styles in upstream data sources.
CREATE TABLE IF NOT EXISTS public.reeds_timeslice_switch_tp_map (
    reeds_timeslice_id INT,
    switch_raw_timepoint_id INT,
    PRIMARY KEY (reeds_timeslice_id, switch_raw_timepoint_id)
);
INSERT INTO reeds_timeslice_switch_tp_map
SELECT reeds_timeslice_id, raw_timepoint_id
FROM reeds_timeslice, switch.raw_timepoint
WHERE (
    ( hour_of_day_start < hour_of_day_end AND
      EXTRACT(HOUR FROM (timestamp_utc - INTERVAL '8 hour')) >= hour_of_day_start AND
      EXTRACT(HOUR FROM (timestamp_utc - INTERVAL '8 hour')) < hour_of_day_end
    ) OR
    ( hour_of_day_start > hour_of_day_end AND
      (
        EXTRACT(HOUR FROM (timestamp_utc - INTERVAL '8 hour')) >= hour_of_day_start OR
        EXTRACT(HOUR FROM (timestamp_utc - INTERVAL '8 hour')) < hour_of_day_end
      )
    )
) AND (
    ( month_start < month_end AND
      EXTRACT(MONTH FROM (timestamp_utc - INTERVAL '8 hour')) >= month_start AND
      EXTRACT(MONTH FROM (timestamp_utc - INTERVAL '8 hour')) <= month_end
    ) OR
    ( month_start > month_end AND
      (
        EXTRACT(MONTH FROM (timestamp_utc - INTERVAL '8 hour')) >= month_start OR
        EXTRACT(MONTH FROM (timestamp_utc - INTERVAL '8 hour')) <= month_end
      )
    ) AND
    month_start IS NOT NULL
)
;

-- 5. Use the timepoints from the SWITCH baseline hourly demand timeseries by load zone (demand_timeseries), join the table of timepoints matched to Reeds timeslices (reeds_timeslice_switch_tp_map), and the heating and cooling degree day slopes adjusted for each SWITCH zone by population density (switch_lz_cool_heat_slopes_reeds_timeslice). This table (switch_lz_timepoint_CC_cool_heat_slopes) now will have the MW per degree day delta for CDD and HDD for each load zone and hourly time point for SWITCH.

CREATE TABLE IF NOT EXISTS public.switch_lz_timepoint_CC_cool_heat_slopes (
    raw_timepoint_id INT,
    timestamp_utc TIMESTAMP WITHOUT TIME ZONE,
    reeds_timeslice_id INT,
    load_zone_id INT,
    load_zone_name VARCHAR,
    type_degree_days VARCHAR (3),
    switch_response_mw_per_delta_dd DOUBLE PRECISION,
    PRIMARY KEY (load_zone_id, raw_timepoint_id, type_degree_days)
);
INSERT INTO public.switch_lz_timepoint_CC_cool_heat_slopes
SELECT a.raw_timepoint_id, a.timestamp_utc, c.reeds_timeslice_id,  a.load_zone_id, a.load_zone_name,  b.type_degree_days, b.switch_response_mw_per_delta_dd
FROM switch.demand_timeseries AS a
JOIN switch.switch_lz_cool_heat_slopes_reeds_timeslice AS b
ON  (a.load_zone_id = b.switch_lz_id
AND a.load_zone_name = b.load_area)
JOIN public.reeds_timeslice_switch_tp_map AS c
ON (a.raw_timepoint_id = c.switch_raw_timepoint_id
AND b.reeds_timeslice_id = c.reeds_timeslice_id)
WHERE a.demand_scenario_id = 115;


--Summmary stat of average hourly MW change (net heating and cooling by load zone, by scenario, then ensemble average) for 2050
CREATE TABLE IF NOT EXISTS public.switch_lz_2050_avg_CC_cool_heat_slopes (
    load_zone_id INT,
    load_zone_name VARCHAR,
    type_degree_days VARCHAR (3),
    year INT,
    avg_switch_response_mw_delta_dd DOUBLE PRECISION,
    PRIMARY KEY (load_zone_id, load_zone_name, year, type_degree_days)
);

INSERT INTO public.switch_lz_2050_avg_CC_cool_heat_slopes
select load_zone_id, load_zone_name, type_degree_days, EXTRACT(YEAR FROM (timestamp_utc)) as year, avg(switch_response_mw_per_delta_dd) as avg_switch_response_mw_delta_dd
from public.switch_lz_timepoint_CC_cool_heat_slopes
where EXTRACT(YEAR FROM (timestamp_utc)) = 2050
group by load_zone_id, load_zone_name, EXTRACT(YEAR FROM (timestamp_utc)), type_degree_days

-- CREATE TABLE IF NOT EXISTS public.switch_lz_2050_avg_CC_net_total_slopes (
--     load_zone_id INT,
--     load_zone_name VARCHAR,
--     year INT,
--     total_avg_switch_response_mw_delta_dd DOUBLE PRECISION,
--     PRIMARY KEY (load_zone_id, load_zone_name, year, type_degree_days)
-- );

-- INSERT INTO public.switch_lz_2050_avg_CC_net_total_slopes
-- select load_zone_id, load_zone_name, year, sum(switch_response_mw_per_delta_dd) as total_avg_switch_response_mw_delta_dd
-- from public.switch_lz_2050_avg_CC_cool_heat_slopes
-- group by load_zone_id, load_zone_name, year


------******************
--- Run queries from here down if additional climate scenarios are added
------******************

-- 6. Create a new table and populate with csv of the daily delta and average daily delta (from 10-year moving window day-of-year average) CDD and HDD by climate scenario for the study period as processed from the R code (should be 15 climate scenarios of daily delta CDD and HDD by load zone, csv is saved as "Daily_delta_CDD_HDD_lz_scenario.csv")
DROP TABLE public.Delta_daily_CDD_HDD_by_climate_scenario;

CREATE TABLE IF NOT EXISTS public.Delta_daily_CDD_HDD_by_climate_scenario (
    Date DATE,
    year INT,
    month INT,
    day INT,
    load_zone VARCHAR,
    Scenario VARCHAR,
    degree_day_type VARCHAR(3),
    degree_day_C DOUBLE PRECISION,
    hist_avg_degree_day_C DOUBLE PRECISION,
    delta_degree_day_C DOUBLE PRECISION,
    avg_delta_degree_day_C DOUBLE PRECISION,
    PRIMARY KEY (Date, Scenario, load_zone, degree_day_type)
);

COPY public.delta_daily_cdd_hdd_by_climate_scenario (date, year, month, day, load_zone, scenario, degree_day_type, degree_day_c, hist_avg_degree_day_c, delta_degree_day_c, avg_delta_degree_day_c) FROM '/Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Load changes for heating and cooling/cdd_hdd/Daily_delta_CDD_HDD_lz_scenario_15scenarios.csv' DELIMITER ',' CSV HEADER QUOTE '\"' ESCAPE '''';

-- 7. Join hourly SWITCH load by load zone by day with delta daily CDD and HDD from R code for each scenario by day. Multiply the heating and cooling day slopes for each hour with the average degree day deltas by day for each load zone to calculate the change in load for each hour, load zone, and scenario
DROP TABLE switch.CDDHDD_load_change_by_climate_scenario;

CREATE TABLE IF NOT EXISTS switch.CDDHDD_load_change_by_climate_scenario (
    raw_timepoint_id INT,
    timestamp_utc TIMESTAMP WITHOUT TIME ZONE,
    timestamp_date DATE,
    reeds_timeslice_id INT,
    load_zone_id INT,
    load_zone_name VARCHAR,
    type_degree_days VARCHAR (3),
    switch_response_mw_per_delta_dd DOUBLE PRECISION,
    avg_delta_degree_day_C DOUBLE PRECISION,
    added_demand_mw  DOUBLE PRECISION,
    climate_scenario VARCHAR,
    PRIMARY KEY (raw_timepoint_id, climate_scenario, load_zone_id,  type_degree_days)
);

INSERT INTO switch.CDDHDD_load_change_by_climate_scenario
SELECT a.raw_timepoint_id, a.timestamp_utc, a.timestamp_utc::timestamp::date AS timestamp_date, a.reeds_timeslice_id,  a.load_zone_id, a.load_zone_name,
a.type_degree_days, a.switch_response_mw_per_delta_dd, b.avg_delta_degree_day_C,
a.switch_response_mw_per_delta_dd * b.avg_delta_degree_day_C as added_demand_mw, b.Scenario
FROM public.switch_lz_timepoint_CC_cool_heat_slopes AS a
JOIN public.Delta_daily_CDD_HDD_by_climate_scenario AS b
ON  (a.load_zone_name = b.load_zone
AND a.timestamp_utc::timestamp::date = b.date
AND a.type_degree_days=b.degree_day_type)
WHERE b.Scenario IN ('CESM1-CAM5_rcp85');
-- WHERE b.Scenario IN ('GFDL-ESM2M_rcp85','GFDL-CM3_rcp85','CMCC-CMS_rcp85','CMCC-CM_rcp85', 'CNRM-CM5_rcp85');


-- this table has 276130980 rows (when 9 scenarios are included)
-- added 153406100 rows with 5 additional scenarios

-- -- QC this join
-- -- 92043660 rows added with 3 climate scenarios

-- -- confirming that 30681220 rows per climate scenario
-- select count(*), climate_scenario
-- from switch.CDDHDD_load_change_by_climate_scenario
-- GROUP BY climate_scenario

-- -- confirming that there are 30681220 rows in the timepoints and heatslopes table by load zone
-- select count(*)
-- from switch_lz_timepoint_CC_cool_heat_slopes
-- GROUP BY load_zone, type_degree_days

-- select count(*), load_zone, type_degree_days
-- from switch_lz_timepoint_CC_cool_heat_slopes
-- GROUP BY load_zone, type_degree_days

-- select count(*), load_zone, type_degree_days, climate_scenario
-- from CDDHDD_load_change_by_climate_scenario
-- GROUP BY load_zone, type_degree_days, climate_scenario

-- -- confirming joins from a sample of 2 days of data
-- select *
-- from switch_lz_timepoint_CC_cool_heat_slopes
-- where date(timestamp_utc) >= '2020-01-01 00:00:00'
-- AND date(timestamp_utc) <= '2020-01-02 00:00:00'
-- order by timestamp_utc, load_zone_name, type_degree_days

-- select *
-- from public.Delta_daily_CDD_HDD_by_climate_scenario
-- where date >= '2020-01-01'
-- AND date <= '2020-01-02'
-- order by date, load_zone, degree_day_type

-- select *
-- from switch.CDDHDD_load_change_by_climate_scenario
-- where date(timestamp_utc) >= '2020-01-01 00:00:00'
-- AND date(timestamp_utc) <= '2020-01-02 00:00:00'
-- order by timestamp_utc, load_zone_name, type_degree_days

-- 8. Calculate the net load change for each hour and load zone and scenario by summing across the HDD and CDD load changes for each hour and load zone.

DROP TABLE switch.Net_load_change_by_climate_scenario_2023;

CREATE TABLE IF NOT EXISTS switch.Net_load_change_by_climate_scenario_2023 (
    raw_timepoint_id INT,
    timestamp_utc TIMESTAMP WITHOUT TIME ZONE,
    timestamp_date DATE,
    load_zone_id INT,
    load_zone_name VARCHAR,
    net_added_demand_mw  DOUBLE PRECISION,
    climate_scenario VARCHAR,
    PRIMARY KEY (load_zone_id, raw_timepoint_id, climate_scenario)
);
INSERT INTO switch.Net_load_change_by_climate_scenario_2023
SELECT raw_timepoint_id, timestamp_utc, timestamp_date, load_zone_id, load_zone_name, sum(added_demand_mw) as net_added_demand_mw, climate_scenario
FROM switch.CDDHDD_load_change_by_climate_scenario
WHERE climate_scenario IN ('CESM1-CAM5_rcp85')
-- WHERE climate_scenario IN ('GFDL-ESM2M_rcp85','GFDL-CM3_rcp85','CMCC-CMS_rcp85','CMCC-CM_rcp85', 'CNRM-CM5_rcp85')
GROUP BY (raw_timepoint_id, timestamp_utc, timestamp_date, load_zone_id, load_zone_name, climate_scenario);


-- -- QC
-- 76703050 rows added with 5 climate scenarios
-- -- should be 15340610 rows per climate scenario
-- select count(*), climate_scenario
-- from Net_load_change_by_climate_scenario_2023
-- GROUP BY climate_scenario


-- 9. Create a new table with the baseline hourly SWITCH load by load zone and the added load for each climate scenario, and create a new column with the total new load
DROP TABLE switch.CC_adjusted_demand_timeseries_2023;

CREATE TABLE IF NOT EXISTS switch.CC_adjusted_demand_timeseries_2023 (
    raw_timepoint_id INT,
    timestamp_utc TIMESTAMP WITHOUT TIME ZONE,
    load_zone_id INT,
    load_zone_name VARCHAR,
    baseline_load_mw DOUBLE PRECISION,
    net_added_demand_mw DOUBLE PRECISION,
    final_demand_mw DOUBLE PRECISION,
    climate_scenario VARCHAR,
    PRIMARY KEY (load_zone_id, raw_timepoint_id, climate_scenario)
);

INSERT INTO switch.CC_adjusted_demand_timeseries_2023
SELECT a.raw_timepoint_id, a.timestamp_utc, a.load_zone_id, a.load_zone_name, a.demand_mw as baseline_load_mw, b.net_added_demand_mw, a.demand_mw + b.net_added_demand_mw as final_demand_mw, b.climate_scenario
FROM switch.demand_timeseries AS a
JOIN switch.Net_load_change_by_climate_scenario_2023 AS b
ON  (a.load_zone_id = b.load_zone_id
AND a.load_zone_name = b.load_zone_name
AND a.raw_timepoint_id = b.raw_timepoint_id
AND a.timestamp_utc = b.timestamp_utc)
WHERE a.demand_scenario_id = 115
AND b.climate_scenario IN ('CESM1-CAM5_rcp85');
-- AND b.climate_scenario IN ('GFDL-ESM2M_rcp85','GFDL-CM3_rcp85','CMCC-CMS_rcp85','CMCC-CM_rcp85', 'CNRM-CM5_rcp85');

-- --QC
-- select count(*), climate_scenario
-- from switch.CC_adjusted_demand_timeseries_2023
-- GROUP BY climate_scenario

-- select distinct(date(timestamp_utc))
-- from switch.demand_timeseries
-- where demand_scenario_id = 115
-- and demand_mw <0
-- order by date(timestamp_utc)

-- select *
-- from switch.CC_adjusted_demand_timeseries_2023
-- where final_demand_mw <0
-- order by date(timestamp_utc)

-- select distinct(date(timestamp_utc))
-- from switch.CC_adjusted_demand_timeseries_2023
-- where final_demand_mw <0
-- order by date(timestamp_utc)

-- 10. Add new demand timeseries and scenario ids to the demand_timeseries table and the demand_scenario table with the new total load

--1 bcc-csm1-1_rcp85
INSERT INTO switch.demand_scenario
SELECT 160 as demand_scenario_id, '[WEAP-SWITCH] bcc-csm1-1_rcp85' as name, 'id 115 + bcc-csm1-1, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 160 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'bcc-csm1-1_rcp85';

--2 CanESM2_rcp85
INSERT INTO switch.demand_scenario
SELECT 161 as demand_scenario_id, '[WEAP-SWITCH] CanESM2_rcp85' as name, 'id 115 + CanESM2, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 161 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'CanESM2_rcp85';

--3 CESM1-BGC_rcp85
INSERT INTO switch.demand_scenario
SELECT 162 as demand_scenario_id, '[WEAP-SWITCH] CESM1-BGC_rcp85' as name, 'id 115 + CESM1-BGC, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 162 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'CESM1-BGC_rcp85';

--4 ACCESS-1.0_rcp85
INSERT INTO switch.demand_scenario
SELECT 163 as demand_scenario_id, '[WEAP-SWITCH] ACCESS-1.0_rcp85' as name, 'id 115 + ACCESS-1.0, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 163 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'ACCESS-1.0_rcp85';

--5 CCSM_rcp85
INSERT INTO switch.demand_scenario
SELECT 164 as demand_scenario_id, '[WEAP-SWITCH] CCSM_rcp85' as name, 'id 115 + CCSM, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 164 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'CCSM_rcp85';

--6 MPI-ESM-LR_rcp85
INSERT INTO switch.demand_scenario
SELECT 165 as demand_scenario_id, '[WEAP-SWITCH] MPI-ESM-LR_rcp85' as name, 'id 115 + MPI-ESM-LR, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 165 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'MPI-ESM-LR_rcp85';

--7 HadGEM2-CC_rcp85
INSERT INTO switch.demand_scenario
SELECT 166 as demand_scenario_id, '[WEAP-SWITCH] HadGEM2-CC_rcp85' as name, 'id 115 + HadGEM2-CC, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 166 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'HadGEM2-CC_rcp85';

--8 HadGEM2-ES_rcp85
INSERT INTO switch.demand_scenario
SELECT 167 as demand_scenario_id, '[WEAP-SWITCH] HadGEM2-ES_rcp85' as name, 'id 115 + HadGEM2-ES, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 167 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'HadGEM2-ES_rcp85';

--9 MIROC5_rcp85
INSERT INTO switch.demand_scenario
SELECT 168 as demand_scenario_id, '[WEAP-SWITCH] MIROC5_rcp85' as name, 'id 115 + MIROC5, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 168 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'MIROC5_rcp85';

--10 "GFDL-CM3_rcp85"
INSERT INTO switch.demand_scenario
SELECT 169 as demand_scenario_id, '[WEAP-SWITCH] GFDL-CM3_rcp85' as name, 'id 115 + GFDL-CM3, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 169 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'GFDL-CM3_rcp85';

--11 "GFDL-ESM2M_rcp85",
INSERT INTO switch.demand_scenario
SELECT 170 as demand_scenario_id, '[WEAP-SWITCH] GFDL-ESM2M_rcp85' as name, 'id 115 + GFDLESM2M, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 170 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'GFDL-ESM2M_rcp85';

--12 "CMCC-CMS_rcp85",
INSERT INTO switch.demand_scenario
SELECT 171 as demand_scenario_id, '[WEAP-SWITCH] CMCC-CMS_rcp85' as name, 'id 115 + CMCC-CMS, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 171 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'CMCC-CMS_rcp85';

--13 "CMCC-CM_rcp85"
INSERT INTO switch.demand_scenario
SELECT 172 as demand_scenario_id, '[WEAP-SWITCH] CMCC-CM_rcp85' as name, 'id 115 + CMCC-CM, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 172 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'CMCC-CM_rcp85';

--14 "CNRM-CM5_rcp85"
INSERT INTO switch.demand_scenario
SELECT 173 as demand_scenario_id, '[WEAP-SWITCH] CNRM-CM5_rcp85' as name, 'id 115 + CNRM-CM5, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 173 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'CNRM-CM5_rcp85';

--15 "CESM1-CAM5_rcp85"
INSERT INTO switch.demand_scenario
SELECT 174 as demand_scenario_id, '[WEAP-SWITCH] CESM1-CAM5_rcp85' as name, 'id 115 + CESM1-CAM5, RCP 8.5, 2023' as description;

INSERT INTO switch.demand_timeseries
(load_zone_id, demand_scenario_id, raw_timepoint_id, load_zone_name, timestamp_utc, demand_mw)
SELECT a.load_zone_id, 174 as demand_scenario_id, a.raw_timepoint_id, a.load_zone_name, a.timestamp_utc, a.final_demand_mw as demand_mw
FROM switch.CC_adjusted_demand_timeseries_2023 as a
WHERE a.climate_scenario = 'CESM1-CAM5_rcp85';

-- 11a. Then download the demand time series for each climate scenario from db to local machine


\COPY (select * from switch.demand_timeseries where demand_scenario_id = 160) to '/home/jszinai/load_ts_160.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_160.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

\COPY (select * from switch.demand_timeseries where demand_scenario_id = 161) to '/home/jszinai/load_ts_161.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_161.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

\COPY (select * from switch.demand_timeseries where demand_scenario_id = 162) to '/home/jszinai/load_ts_162.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_162.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

--ACCESS
\COPY (select * from switch.demand_timeseries where demand_scenario_id = 163) to '/home/jszinai/load_ts_163.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_163.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

CCSM
\COPY (select * from switch.demand_timeseries where demand_scenario_id = 164) to '/home/jszinai/load_ts_164.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_164.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

-MPI-ESM-LR
\COPY (select * from switch.demand_timeseries where demand_scenario_id = 165) to '/home/jszinai/load_ts_165.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_165.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

--HadGEM2-CC
\COPY (select * from switch.demand_timeseries where demand_scenario_id = 166) to '/home/jszinai/load_ts_166.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_166.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

--HadGEM2-ES
\COPY (select * from switch.demand_timeseries where demand_scenario_id = 167) to '/home/jszinai/load_ts_167.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_167.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

--MIROC5
\COPY (select * from switch.demand_timeseries where demand_scenario_id = 168) to '/home/jszinai/load_ts_168.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_168.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

GFDL-cm3
\COPY (select * from switch.demand_timeseries where demand_scenario_id = 169) to '/home/jszinai/load_ts_169.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_169.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

GFDL -E3SM2
\COPY (select * from switch.demand_timeseries where demand_scenario_id = 170) to '/home/jszinai/load_ts_170.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_170.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

CMCC-CMS
\COPY (select * from switch.demand_timeseries where demand_scenario_id = 171) to '/home/jszinai/load_ts_171.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_171.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

CMCC-CM
\COPY (select * from switch.demand_timeseries where demand_scenario_id = 172) to '/home/jszinai/load_ts_172.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_172.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

CNRM-CM5

\COPY (select * from switch.demand_timeseries where demand_scenario_id = 173) to '/home/jszinai/load_ts_173.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_173.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data

CESM1-CAM5
\COPY (select * from switch.demand_timeseries where demand_scenario_id = 174) to '/home/jszinai/load_ts_174.csv' (FORMAT CSV, HEADER);

rsync -avzh jszinai@shasta-db1.ream.ucsd.edu:/home/jszinai/load_ts_174.csv /Users/juliaszinai/Dropbox/Linux_work/switch3/WECC/Timesample/data


-- 11b. Run the timesampling python script which will sample the new time series to create new sampling ids for each scenario



-- 12. Create a new scenario for each new climate scenario that has the new demand_scenario_id and the time sampling ids

-- CREATING A NEW 0 CARBON WECC BASELINE SCENARIO with PLANNING RESERVES ENABLED (scenario 202) for UCSD db


insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 207 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, bcc-csm1-1_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for bcc-csm1-1_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 160
WHERE scenario_id=207;

UPDATE switch.scenario SET
study_timeframe_id = 19,
time_sample_id = 18
WHERE scenario_id=207;


--CanESM2_rcp85
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 208 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, CanESM2_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for CanESM2_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 161
WHERE scenario_id=208;

UPDATE switch.scenario SET
study_timeframe_id = 20,
time_sample_id = 19
WHERE scenario_id=208;

-- CESM1-BGC_rcp85
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 209 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, CESM1-BGC_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for CESM1-BGC_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 162,
study_timeframe_id = 21,
time_sample_id = 20
WHERE scenario_id=209;

-- ACCESS-1.0_rcp85
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 235 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, ACCESS-1.0_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for ACCESS-1.0_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 163,
study_timeframe_id = 22,
time_sample_id = 21
WHERE scenario_id=235;

-- CCSM_rcp85
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 236 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, CCSM_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for CCSM_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 164,
study_timeframe_id = 23,
time_sample_id = 22
WHERE scenario_id=236;

-- MPI-ESM-LR_rcp85
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 237 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, MPI-ESM-LR_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for MPI-ESM-LR_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 165;
study_timeframe_id = 24,
time_sample_id = 23
WHERE scenario_id=237;


-- HadGEM2-CC_rcp85

insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 238 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, HadGEM2-CC_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for HadGEM2-CC_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 166,
study_timeframe_id = 31,
time_sample_id = 24
WHERE scenario_id=238;

-- HadGEM2-ES_rcp85
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 239 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, HadGEM2-ES_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for HadGEM2-ES_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 167,
study_timeframe_id = 32,
time_sample_id = 31
WHERE scenario_id=239;

-- MIROC5_rcp85

insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 240 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, MIROC5_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for MIROC5_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 168,
study_timeframe_id = 33,
time_sample_id = 32
WHERE scenario_id=240;

-- GFDL-CM3

insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 260 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, GFDL-CM3_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for GFDL-CM3_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 169,
study_timeframe_id = 34,
time_sample_id = 33
WHERE scenario_id=260;

-- GFDL-ESM2M
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 261 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, GFDL-ESM2M_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for GFDL-ESM2M_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 170,
study_timeframe_id = 35,
time_sample_id = 34
WHERE scenario_id=261;

-- CMCC-CMS
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 262 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, CMCC-CMS_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for CMCC-CMS_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 171,
study_timeframe_id = 36,
time_sample_id = 35
WHERE scenario_id=262;

-- CMCC-CM

insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 263 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, CMCC-CM_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for CMCC-CM_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 172,
study_timeframe_id = 37,
time_sample_id = 36
WHERE scenario_id=263;

-- CNRM-CM5
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 264 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, CNRM-CM5_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for CNRM-CM5_rcp85 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 173,
study_timeframe_id = 38,
time_sample_id = 37
WHERE scenario_id=264;

-- CESM1-CAM5
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 265 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 5y24h, CESM1-CAM5_rcp85',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, with load adjusted for CESM1-CAM5_rcp855 CDD_HDD, all else id 202 (24hr, 5 yr periods)',
demand_scenario_id = 174,
study_timeframe_id = 39,
time_sample_id = 38
WHERE scenario_id=265;





---------------------Follow up analysis for Nature Communications paper revisions
----RUN new test scenario with hourly sampling of just 2050, for the ACCESS load

--new scenario with baseline load time series but sampled hourly for 2050
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 266 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=202;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 2050 hourly',
description = 'Baseline with 0 carbon cap for WECC and CA by 2045, sampled hourly for just 2050, all else id 202',
demand_scenario_id = 115,
study_timeframe_id = 40,
time_sample_id = 12
WHERE scenario_id=266;

---new scenario with load adjusted for ACCESS and sampled hourly for 2050
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 267 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=266;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 2050 hourly, ACCESS-1.0_rcp85',
description = 'ACCESS load with 0 carbon cap for WECC and CA by 2045, sampled hourly for just 2050, all else id 266',
demand_scenario_id = 163,
WHERE scenario_id=267;

---new scenario with load adjusted for ACCESS and sampled every 4 hours daily for 2050
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 268 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=267;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 2050 4-hourly, ACCESS-1.0_rcp85',
description = 'ACCESS load with 0 carbon cap for WECC and CA by 2045, sampled 4-hourly for just 2050, all else id 266',
demand_scenario_id = 163,
study_timeframe_id = 25,
time_sample_id = 25
WHERE scenario_id=268;

---new scenario with baseline load sampled every 4 hours daily for 2050
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 269 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=268;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 2050 4-hourly',
description = 'Baseline load with 0 carbon cap for WECC and CA by 2045, sampled 4-hourly for just 2050, all else id 268',
demand_scenario_id = 115,
study_timeframe_id = 25,
time_sample_id = 25

---new scenario with baseline load sampled every 2 hours daily for 2050
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 270 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=269;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 2050 2-hourly',
description = 'Baseline load with 0 carbon cap for WECC and CA by 2045, sampled 2-hourly for just 2050, all else id 269',
demand_scenario_id = 115,
study_timeframe_id = 9,
time_sample_id = 9
where scenario_id=270;

---new scenario with ACCESS load sampled every 2 hours daily for 2050
insert into switch.scenario (scenario_id, name, description, study_timeframe_id, time_sample_id, demand_scenario_id, fuel_simple_price_scenario_id, generation_plant_scenario_id, generation_plant_cost_scenario_id, generation_plant_existing_and_planned_scenario_id, hydro_simple_scenario_id, carbon_cap_scenario_id, supply_curves_scenario_id, regional_fuel_market_scenario_id, rps_scenario_id, enable_dr, enable_ev, transmission_base_capital_cost_scenario_id, ca_policies_scenario_id, enable_planning_reserves, generation_plant_technologies_scenario_id, variable_o_m_cost_scenario_id, wind_to_solar_ratio,transmission_scenario_id)

select 271 as scenario_id, s.name, s.description, s.study_timeframe_id, s.time_sample_id, s.demand_scenario_id, s.fuel_simple_price_scenario_id, s.generation_plant_scenario_id, s.generation_plant_cost_scenario_id, s.generation_plant_existing_and_planned_scenario_id, s.hydro_simple_scenario_id, s.carbon_cap_scenario_id, s.supply_curves_scenario_id,s.regional_fuel_market_scenario_id, s.rps_scenario_id, s.enable_dr, s.enable_ev, s.transmission_base_capital_cost_scenario_id, s.ca_policies_scenario_id, s.enable_planning_reserves, s.generation_plant_technologies_scenario_id, s.variable_o_m_cost_scenario_id, s.wind_to_solar_ratio,s.transmission_scenario_id
from switch.scenario s
where scenario_id=270;

UPDATE switch.scenario SET
name = '[SWITCH_WEAP] 0 CO2 2050 2hourly, ACCESS-1.0_rcp85',
description = 'ACCESS load with 0 carbon cap for WECC and CA by 2045, sampled 2-hourly for just 2050, all else id 270',
demand_scenario_id = 163,
study_timeframe_id = 9,
time_sample_id = 9
where scenario_id=271;