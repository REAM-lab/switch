-- Winter 2023, UC San Diego
-- New cost scenarios for DOE wave energy project
-- Natalia Gonzalez, Patricia Hidalgo-Gonzalez



drop table public.gen_build_cost_scenarios_wave_offshore_wind_industry_sites;

create table public.gen_build_cost_scenarios_wave_offshore_wind_industry_sites(
index bigint, -- I added this column because the csv came with an index col, so it was simpler to create it here than delete it from the csv
GENERATION_PROJECT double precision,
build_year double precision,
gen_fixed_om double precision,
gen_overnight_cost double precision,
scenario_id int,
primary key (scenario_id, GENERATION_PROJECT, build_year)
);




COPY public.gen_build_cost_scenarios_wave_offshore_wind_industry_sites
FROM '/home/n7gonzalez/switch/wave_energy/all_scenarios_gen_build_cost_wave_offshore_wind.csv'
DELIMITER ',' NULL AS 'NULL' CSV HEADER;
-- ran until here ----------------------------------------------------------------------------------

-- previous scenario:
-- insert into switch.generation_plant_cost_scenario
-- values(49,'SWITCH 2020 Baseline costs from id 25. DOE_wave',
-- 			  'same costs as id 25, and costs for wave energy/offshore wind candidates from NREL ATB and WPTO wave hindcast Natalia 5x5 runs');

-- --------------------------------------------------------------------------------
-- Even though in 2023_01_20_wave_energy_offshore_wind_data_high_res.sql we uploaded costs for scenario 1 
-- (generation_plant_cost_scenario_id=49), we will do it again here to use the same wording for the 
-- complete set of scenarios.

-- scenario 1 from csv
-- /home/n7gonzalez/switch/wave_energy/all_scenarios_gen_build_cost_wave_offshore_wind.csv



do
$$
begin
for scenario_index in 1..25 loop
raise notice 'scenario_id: %', scenario_index;

insert into switch.generation_plant_cost_scenario
values(49 + scenario_index, concat('2020 BAU id 25. Wave/offwind compare. scen_id = ', scenario_index),
 			  concat('same costs as id 25, wave energy 2020 (RM6, Sandia div10), wave energy NREL ATB 2022 utility solar/land wind/2020/2050, and offshore wind NREL ATB 2022 very adv/adv/mod/con/very con (scenario_id = ', scenario_index, ' from csv), Natalias paper'));

-- One query per scenario
insert into switch.generation_plant_cost
select 49 + scenario_index as generation_plant_cost_scenario_id, generation_plant_id, build_year, fixed_o_m, overnight_cost,
storage_energy_capacity_cost_per_mwh
from switch.generation_plant_cost
where generation_plant_cost_scenario_id = 25 -- 25 is the previous baseline scenario used for CEC LDES 2020-2022
union
select 49 + scenario_index as generation_plant_cost_scenario_id, t.generation_project as generation_plant_id,
build_year, gen_fixed_om as fixed_o_m, gen_overnight_cost as  overnight_cost,
NULL as storage_energy_capacity_cost_per_mwh
from public.gen_build_cost_scenarios_wave_offshore_wind_industry_sites as t
where t.scenario_id = scenario_index;

-- new switch scenario
insert into switch.scenario
values (209 + scenario_index, 
		concat('[DOE wave energy v2] scen', scenario_index, ' wave offwind sites'), 
		concat('New: wave energy and offshore wind sites of industry Ryan, highwave ATB offwind scenario_id= ', scenario_index, ' from Natis csv. Same: WECC zero emissions, no RPS, NREL ATB 2020, updated gen listings (env cat 3), 2017 fuel costs from EIA, 2018 dollars, supply curve for Bio_Solid'),
		3, -- study_timeframe_id
		3, -- time_sample_id
		115, -- demand_scenario_id
		4, -- fuel_simple_price_scenario, without Bio_Solid costs, because they are provided by supply curve
		27, -- generation_plant_scenario_id (27: New scenario from Natalias work, 101 sites of colocated wave and wind)
		49 + scenario_index, -- generation_plant_cost_scenario_id HEREEEEEEEEEEEEEEEEE it gets updated with the index
		21, -- generation_plant_existing_and_planned_scenario_id
		23, -- hydro_simple_scenario_id
		92, -- carbon_cap_scenario_id
		2, -- supply_curve_scenario_id
		1, -- regional_fuel_market_scenario_id
		NULL, -- rps_scenario_id
		NULL, --enable_dr
		NULL, --enable_ev
		2, --transmission_base_capital_cost_scenario_id
		NULL, --ca_policies_scenario_id
		0, --enable_planning_reserves
		2, --generation_plant_technologies_scenario_id
		3, --variable_o_m_cost_scenario_id
		NULL, --wind_to_solar_ratio
		2 --transmission_scenario_id
		);

end loop;
end;
$$




