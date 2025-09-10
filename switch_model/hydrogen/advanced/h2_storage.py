# Copyright (c) 2016-2017 The Switch Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0, which is in the LICENSE file.

"""
This module defines hydrogen storage technologies. It adds components for deciding how much energy to build into
storage, when to charge, energy accounting, etc.

INPUT FILE FORMAT
    Import storage and storage compressor parameters.

    h2_storage_projects_info.csv
        H2_STORAGE_PROJECT, h2stor_load_zone, h2stor_life_years, 
        h2stor_maximum_size_kg, h2stor_leakage_rate, h2stor_type
    Optional columns are: 
        h2stor_max_cycles_per_year

    h2_storage_build_costs.csv
        H2_STORAGE_PROJECT, build_year, h2stor_overnight_cost_per_kg, 
        h2stor_fixed_om_cost_per_kg
    
    h2_storage_predetermined.csv
        H2_STORAGE_PROJECT, build_year, h2stor_predetermined_kg
        
    h2_storage_compressors.csv
        h2stor_type, comp_overnight_cost_per_mw, comp_fixed_om_cost_per_mw_yr, 
        comp_mwh_per_kg, h2stor_comp_life_years
    Optional columns are:
        h2stor_cap_frac_withdraw_limit

"""
from pyomo.environ import *
import os, collections
from switch_model.financials import capital_recovery_factor as crf
from switch_model.reporting import write_table
from switch_model.utilities.scaling import get_assign_default_value_rule

dependencies = (
    "switch_model.timescales",
    "switch_model.hydrogen.advanced.h2_timescales",
    "switch_model.balancing.load_zones",
    "switch_model.financials",
    "switch_model.energy_sources.properties",
    "switch_model.hydrogen.advanced.h2_production_build"
)

def define_components(mod):
    """
    
    -- SETS AND PARAMETERS --

    H2_STORAGE_PROJECTS is the set of H2 storage candidate projects, which are of different types 
    (gas_hydrogen_tank, hard_rock, salt_cavern). Shorthand for an element 
    from this set is "s" for storage.  
    
    H2_STORAGE_BLD_YRS is the set of H2 storage projects and years which they may be built 
    (investment periods and predetermined build years). Shorthand for an element from this set 
    is "(s, bld_yr)". 
    
    CAPACITY_LIMITED_H2_STORAGE is a subset of H2_STORAGE_BLD_YRS that have specified a 
    maximum capacity in kg for the respective bld_yr.
    
    PREDETERMINED_H2_STORAGE_BLD_YRS is the set of predetermined H2 storage projects and years
    which the project built the predetermined capacity. 
    
    BLD_YRS_FOR_H2_STORAGE[s] is the set of years (predetermined and not) that a given storage
    project [s] can be built.
    
    BLD_YRS_FOR_H2_STOR_PERIOD[s, p] is the set of build years that could be online in the given 
    period [p] for the given H2 storage project [s].
    
    PERIODS_FOR_H2_STOR[s] is set of periods when a H2 storage project [s] is available to use.
    
    H2_STORAGE_PERIODS[s, p] is the set of storage project and period tuples (s, p) corresponding 
    to all possible combinations of H2 storage projects and periods which each project is 
    available to use.
    
    HGTS_FOR_H2_STORAGE[s] is defined as the set of hydrogen timeseries [HGTS] that a given H2 
    storage project [s] is available to use.
    
    TPS_FOR_H2_STORAGE[s] is defined as the set of timepoints that a given H2 storage project [s] 
    is available to use.
    
    H2_STORAGE_TPS is defined as the set of tuples (s, tp) for every combination of H2 storage 
    project [s] and corresponding timepoint (tp) which that project is available to use.

    h2stor_predetermined_kg[(s, bld_yr) in PREDETERMINED_H2_STORAGE_BLD_YRS] is the 
    amount of H2 storage in kg that has either been installed previously, or is slated for 
    installation and is not a free decision variable. This is analogous to 
    gen_predetermined_cap, but in units of hydrogen storage capacity (kg) rather than power (MW). 

    h2stor_maximum_size_kg[s] is a parameter which specifies the maximum possible H2 storage 
    capacity that can be built for a given H2 storage project, in kg of H2. 
    If the project has predetermined capacity but can be expanded, then the amount of additional 
    capacity that can be built should be the difference between the h2stor_maximum_size_kg[s] and 
    h2stor_predetermined_kg[s, bld_yr]. 

    h2stor_load_zone[s] is a parameter which specifies which load zone the H2 storage project 
    corresponds to/falls within.

    h2stor_type[s] is a parameter that identifies whether the H2 storage is a gas_hydrogen_tank, 
    hard_rock_cavern, or salt_cavern. 

    h2stor_life_years[s] is a parameter which specifies the lifetime of the H2 storage project in 
    years.

    h2stor_leakage_rate[s] is the rate (as a percent fraction) in which H2 leaks out
    of storage as a percentage. This is tracked in the H2Storage_Zonal_H2_Leakage expression,
    which calculates total H2 leakage in metric tons of H2 per zone at each tp, which is summed
    and addeed to the Period_Fugitive_H2 dynamic list, which tracks H2 leakage across all H2 system 
    components.
    
    h2stor_overnight_cost_per_kg[(s, bld_yr) in H2_STORAGE_BLD_YRS] is the overnight 
    capital cost per kg of hydrogen capacity for building the given storage technology 
    installed in the given investment period in $/kg.
    
    h2stor_fixed_om_cost_per_kg[(s, bld_yr) in H2_STORAGE_BLD_YRS] is the fixed O&M cost
    per kg of hydrogen capacity per year for building the given storage technology installed 
    in the given investment period in $/kg per year.
    
    h2stor_max_cycles_per_year[s], if specified, restricts
    the number of charge/discharge cycles each H2 storage project can perform
    per year; one cycle is defined as discharging an amount of H2
    equal to the H2 storage capacity of the project.  

    -- CONSTRUCTION --

    BuildH2Storage[(s, bld_yr) in H2_STORAGE_BLD_YRS] is a decision variable for how much 
    H2 capacity to build onto a storage project. This is analogous to BuildGen, but for kg 
    of hydrogen rather than power capacity.

    H2StorageFixedCost[PERIODS] is an expression of the annual fixed costs incurred by the 
    BuildH2Storage decision for each period in the set PERIODS.

    H2StorageCapacity[s, p] is an expression describing the cumulative available H2 
    capacity of BuildH2Storage for a given H2 storage project [s] in a given period [p]. 
    This is analogous to GenCapacity, but in units of kg of hydrogen.

    FillH2Storage[(s, tp) for tp in m.TPS_IN_HGTS[hgts]] is a dispatch decision of how 
    much to fill a hydrogen storage project in each timepoint in MW of H2.
    
    *Note: MW of H2 can be converted to a kg/h flow rate using the LHV of H2 of 33.32 kWh/kg
    from https://www.engineeringtoolbox.com/fuels-higher-calorific-values-d_169.html
    and https://sci-hub.kvnp.top/10.1016/j.ijhydene.2019.10.080. 
    (Example: Say a storage project is filled at a rate of 1 MW of H2 at timepoint t. 
    -> 1 MW of H2 * (1 kg of H2/33.32 kWh) * (1,000 kW/1 MW) =~ 30.012 kg of H2/h

    H2StorageTotalFill[LOAD_ZONE, TIMEPOINT] is an expression describing the aggregate 
    impact of FillH2Storage in each load zone and timepoint. This gets appended to the 
    Zone_H2_Withdrawals dynamic list, since filling a storage project is a withdrawal from 
    the load zone hydrogen supply in the H2 balance equation.
    
    FillH2StorUpperLimit[(s, t) in H2_STORAGE_TPS]

    Fill_H2_Storage_Upper_Limit[(s, t) in H2_STORAGE_TPS] constrains FillH2Storage for each storage
    project [s] in each timepoint [t] to the maximum fill rate as defined by 
    FillH2StorUpperLimit[s, t].
    
    WithdrawH2Storage[(s, t) in H2_STORAGE_TPS] is a dispatch decision variable for how 
    much to fill a hydrogen storage project in each timepoint in MW of H2.
    
    H2StorageTotalWithdrawal[LOAD_ZONES, TIMEPOINTS] is an expression that calculates the total
    withdrawal of H2 for each load zone at each timepoint as a consequence of 
    WithdrawH2Storage[s, t], accounting for leakage according to h2stor_leakage_rate[s]. This 
    gets appended to the Zone_H2_Injections dynamic list, since withdrawing H2 from a storage 
    project contributes to the load zone hydrogen supply in the H2 balance equation.
    
    H2Storage_Zonal_H2_Leakage[LOAD_ZONES, TIMEPOINTS] is an expression that calculates the total
    H2 leakage (fugitive H2) in metric ton of H2, which we assume is proportional to the amount
    of H2 withdrawn from each storage project at each timepoint by a factor of 
    h2stor_leakage_rate[s]. This is appended to the Period_Fugitive_H2 dynamic list to keep track of 
    fugitive H2 emissions, which have a high global warming potential (GWP).

    H2StateOfFill[(s, t) in H2_STORAGE_TPS] is a decision variable for controlling the state of 
    "charge" or state of fill for a given H2 storage project [s] at a given timepoint [t]. The 
    state of fill is measured in kg of H2.

    H2_Track_State_Of_Fill[(s, t) in H2_STORAGE_TPS] constrains H2StateOfFill based on the 
    H2StateOfFill in the previous timepoint, FillH2Storage, and WithdrawH2Storage.

    H2_State_Of_Fill_Upper_Limit[(s, t) in H2_STORAGE_TPS] constrains H2StateOfFill of storage 
    project [s] in timepoint [t] based on kg of installed H2 capacity for the corresponding 
    project and period.
    
    H2_Storage_Cycle_Limit[(s, p) in H2_STORAGE_PERIODS] constrains the sum of withdrawn H2 in
    period [p], converted to kg, by the amount of H2 in kg that corresponds to the 
    h2stor_max_cycles_per_year[s] multiplied by the capacity of storage project [s] in kg 
    and the number of years in period [p]. One cycle is considered a withdrawal of H2 equal to
    the capacity of the storage project. For example, if a storage project has a capacity of 
    100 kg and a max of 10 cycles per year in a 10 year period, then that project can withdraw
    up to 10,000 kg of H2 in that period.
    
    """
    
    # inputs from h2_storage_projects_info.csv
    mod.H2_STORAGE_PROJECTS = Set(dimen=1, input_file="h2_storage_projects_info.csv")
    mod.h2stor_load_zone = Param(mod.H2_STORAGE_PROJECTS, input_file="h2_storage_projects_info.csv",
                              within=mod.LOAD_ZONES)
    mod.h2stor_type = Param(mod.H2_STORAGE_PROJECTS, input_file="h2_storage_projects_info.csv")
    mod.H2_STORAGE_TECHNOLOGIES = Set(ordered=False, 
                                      dimen=1,
                                      initialize=lambda m:
                                          {m.h2stor_type[s] for s in m.H2_STORAGE_PROJECTS})
    mod.h2stor_life_years = Param(mod.H2_STORAGE_PROJECTS, input_file="h2_storage_projects_info.csv",
                            within=PositiveIntegers)
    mod.h2stor_leakage_rate = Param(mod.H2_STORAGE_PROJECTS, input_file="h2_storage_projects_info.csv",
                            within=PercentFraction)
    
    mod.h2stor_maximum_size_kg = Param(mod.H2_STORAGE_PROJECTS, input_file="h2_storage_projects_info.csv",
                                       input_optional=True, within=NonNegativeReals)
    mod.CAPACITY_LIMITED_H2_STORAGE = Set(within=mod.H2_STORAGE_PROJECTS)
    mod.h2stor_max_cycles_per_year = Param(
        mod.H2_STORAGE_PROJECTS,
        within=NonNegativeReals,
        input_file="h2_storage_projects_info.csv",
        default=float("inf"),
    )

    # inputs from h2_storage_predetermined.csv
    mod.PREDETERMINED_H2_STORAGE_BLD_YRS = Set(
	    input_file="h2_storage_predetermined.csv",
        input_optional=True,
        dimen=2
	)
    mod.h2stor_predetermined_kg = Param(
        mod.PREDETERMINED_H2_STORAGE_BLD_YRS,
        input_file="h2_storage_predetermined.csv",
        within=NonNegativeReals)
    
    # inputs from h2_storage_build_costs.csv
    mod.H2_STORAGE_BLD_YRS = Set(
        dimen=2,
        input_file="h2_storage_build_costs.csv",
        validate=lambda m, s, bld_yr: (
            (s, bld_yr) in m.PREDETERMINED_H2_STORAGE_BLD_YRS or
            (s, bld_yr) in m.H2_STORAGE_PROJECTS * m.PERIODS))
    mod.h2stor_overnight_cost_per_kg = Param(
        mod.H2_STORAGE_BLD_YRS,
        input_file="h2_storage_build_costs.csv",
        within=NonNegativeReals,
    )
    mod.h2stor_fixed_om_cost_per_kg = Param(
        mod.H2_STORAGE_BLD_YRS,
        input_file="h2_storage_build_costs.csv",
        within=NonNegativeReals,
    )
    mod.min_data_check("h2stor_overnight_cost_per_kg","h2stor_fixed_om_cost_per_kg")
    

    def h2stor_build_can_operate_in_period(m, s, build_year, period):
        # If a period has the same name as a predetermined build year then we have a problem.
        # For example, consider what happens if we have both a period named 2020
        # and a predetermined build in 2020. In this case, "build_year in m.PERIODS"
        # will be True even if the project is a 2020 predetermined build.
        # This will result in the "online" variable being the start of the period rather
        # than the prebuild year which can cause issues such as the project retiring too soon.
        # To prevent this we've added the h2stor_no_predetermined_bld_yr_vs_period_conflict BuildCheck below.
        if build_year in m.PERIODS:
            online = m.period_start[build_year]
        else:
            online = build_year
        retirement = online + m.h2stor_life_years[s]
        # Previously the code read return online <= m.period_start[period] < retirement
        # However using the midpoint of the period as the "cutoff" seems more correct so
        # we've made the switch.
        return online <= m.period_start[period] + 0.5 * m.period_length_years[period] < retirement

    # This verifies that a predetermined build year doesn't conflict with a period since if that's the case
    # gen_build_can_operate_in_period will mistaken the prebuild for an investment build
    # (see note in h2stor_build_can_operate_in_period)
    mod.h2stor_no_predetermined_bld_yr_vs_period_conflict = BuildCheck(
        mod.PREDETERMINED_H2_STORAGE_BLD_YRS, mod.PERIODS,
        rule=lambda m, bld_yr, p: bld_yr != p
    )

    mod.BLD_YRS_FOR_H2_STORAGE = Set(
        mod.H2_STORAGE_PROJECTS,
        ordered=False,
        initialize=lambda m, s: set(
            bld_yr for (h2stor, bld_yr) in m.H2_STORAGE_BLD_YRS if h2stor == s
        )
    )

    # The set of build years that could be online in the given period
    # for the given H2 storage project.
    mod.BLD_YRS_FOR_H2_STORAGE_PERIOD = Set(
        mod.H2_STORAGE_PROJECTS, mod.PERIODS,
        ordered=False,
        initialize=lambda m, s, period: set(
            bld_yr for bld_yr in m.BLD_YRS_FOR_H2_STORAGE[s]
            if h2stor_build_can_operate_in_period(m, s, bld_yr, period)))
    # The set of periods when a H2 storage tech is available to use
    mod.PERIODS_FOR_H2_STOR = Set(
        mod.H2_STORAGE_PROJECTS,
        initialize=lambda m, s: [p for p in m.PERIODS if len(m.BLD_YRS_FOR_H2_STORAGE_PERIOD[s, p]) > 0]
    )

    def H2_STORAGE_IN_ZONE_init(m, z):
        if not hasattr(m, 'H2_STORAGE_IN_ZONE_dict'):
            m.H2_STORAGE_IN_ZONE_dict = {_z: [] for _z in m.LOAD_ZONES}
            for s in m.H2_STORAGE_PROJECTS:
                m.H2_STORAGE_IN_ZONE_dict[m.h2stor_load_zone[s]].append(s)
        result = m.H2_STORAGE_IN_ZONE_dict.pop(z)
        if not m.H2_STORAGE_IN_ZONE_dict:
            del m.H2_STORAGE_IN_ZONE_dict
        return result
    mod.H2_STORAGE_IN_ZONE = Set(
        mod.LOAD_ZONES,
        initialize=H2_STORAGE_IN_ZONE_init
    )

    def bounds_BuildH2Storage(mod, s, bld_yr):
        if((s, bld_yr) in mod.PREDETERMINED_H2_STORAGE_BLD_YRS):
            return (mod.h2stor_predetermined_kg[s, bld_yr],
                    mod.h2stor_predetermined_kg[s, bld_yr])
        elif(s in mod.CAPACITY_LIMITED_H2_STORAGE):
            # This does not replace Max_Build_Potential because
            # Max_Build_Potential applies across all build years.
            return (0, mod.h2stor_maximum_size_kg[s])
        else:
            return (0, None)
    mod.BuildH2Storage = Var(
        mod.H2_STORAGE_BLD_YRS,
        within=NonNegativeReals,
        bounds=bounds_BuildH2Storage)
    
    # Some projects are retired before the first study period, so they
    # don't appear in the objective function or any constraints.
    # In this case, pyomo may leave the variable value undefined even
    # after a solve, instead of assigning a value within the allowed
    # range. This causes errors in the Progressive Hedging code, which
    # expects every variable to have a value after the solve. So as a
    # starting point we assign an appropriate value to all the existing
    # projects here.
    mod.BuildH2Storage_assign_default_value = BuildAction(
        mod.PREDETERMINED_H2_STORAGE_BLD_YRS,
        rule=get_assign_default_value_rule("BuildH2Storage", "h2stor_predetermined_kg"))

    mod.H2_STORAGE_PERIODS = Set(
        dimen=2,
        initialize=lambda m:
            [(s, p) for s in m.H2_STORAGE_PROJECTS for p in m.PERIODS_FOR_H2_STOR[s]])

    mod.H2StorageCapacity = Expression(
        mod.H2_STORAGE_PROJECTS, mod.PERIODS,
        rule=lambda m, s, period: sum(
            m.BuildH2Storage[s, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_H2_STORAGE_PERIOD[s, period]))
    
    # We use a scaling factor to improve the numerical properties
    # of the model. The scaling factor was determined using trial
    # and error and this tool https://github.com/staadecker/lp-analyzer.
    # Learn more by reading the documentation on Numerical Issues.
    # Note we removed the ability to specify a minumum build capacity 
    # for H2 production projects as to avoid binary variables
    max_build_potential_scaling_factor = 1e-1
    mod.Max_H2_Stor_Build_Potential = Constraint(
        mod.CAPACITY_LIMITED_H2_STORAGE, mod.PERIODS,
        rule=lambda m, s, p: (
                m.h2stor_maximum_size_kg[s] * max_build_potential_scaling_factor >= 
                m.H2StorageCapacity[s, p] * max_build_potential_scaling_factor))

    mod.HGTS_FOR_H2_STORAGE = Set(
        mod.H2_STORAGE_PROJECTS,
        within=mod.HGTS,
        initialize=lambda m, s: (
            hgts for p in m.PERIODS_FOR_H2_STOR[s] for hgts in m.HGTS_IN_PERIOD[p]
        )
    )
    
    mod.TPS_FOR_H2_STORAGE = Set(
        mod.H2_STORAGE_PROJECTS,
        within=mod.TIMEPOINTS,
        initialize=lambda m, s: (
            tp for hgts in m.HGTS_FOR_H2_STORAGE[s] for tp in m.TPS_IN_HGTS[hgts]
        )
    )

    mod.H2_STORAGE_TPS = Set(
        dimen=2,
        initialize=lambda m: (
            (s, tp) for s in m.H2_STORAGE_PROJECTS for tp in m.TPS_FOR_H2_STORAGE[s]
        ),
    )
    
    # -- H2torage costs --

    # Summarize capital costs of H2 storage for the objective function
    mod.H2StorageFixedCost = Expression(
        mod.PERIODS,
        rule=lambda m, p: sum(
            sum(
                m.BuildH2Storage[s, bld_yr]
                * m.h2stor_overnight_cost_per_kg[s, bld_yr]
                * crf(m.interest_rate, m.h2stor_life_years[s])
                + m.BuildH2Storage[s, bld_yr] * m.h2stor_fixed_om_cost_per_kg[s, bld_yr]
                for bld_yr in m.BLD_YRS_FOR_H2_STORAGE_PERIOD[s, p]
            )
            for s in m.H2_STORAGE_PROJECTS
        ),
    )
    mod.Cost_Components_Per_Period.append("H2StorageFixedCost")

    # -- H2 storage compressors -- 
    mod.h2stor_comp_overnight_cost_per_mw = Param(mod.H2_STORAGE_TECHNOLOGIES, within=NonNegativeReals,
		input_file="h2_storage_compressors.csv", input_column="comp_overnight_cost_per_mw")
    mod.h2stor_comp_fixed_om_cost_per_mw_yr = Param(mod.H2_STORAGE_TECHNOLOGIES, within=NonNegativeReals,
		input_file="h2_storage_compressors.csv", input_column="comp_fixed_om_cost_per_mw_yr")
	# compressor electric load
    mod.h2stor_comp_mwh_per_kg = Param(mod.H2_STORAGE_TECHNOLOGIES, within=NonNegativeReals,
		input_file="h2_storage_compressors.csv", input_column="comp_mwh_per_kg")
	# optional withdrawal limit per H2 storage type as a fraction of H2 storage project capacity per hour 
	# example: [fraction between 0 and 1] * [kg of storage capacity] * [33.32 kWh/kg of H2] * [1 MWh/1000 kWh] = [MW of H2]
	# the resulting MW of H2 would be an upper limit on the withdrawal rate of the H2 storage technology
    mod.h2stor_cap_frac_withdraw_limit = Param(mod.H2_STORAGE_TECHNOLOGIES, within=NonNegativeReals,
		default=1, input_file="h2_storage_compressors.csv", input_column="h2stor_cap_frac_withdraw_limit")
    mod.h2stor_comp_life_years = Param(mod.H2_STORAGE_TECHNOLOGIES, within=NonNegativeReals,
		default=25, input_file="h2_storage_compressors.csv", input_column="h2stor_comp_life_years")
    mod.min_data_check("h2stor_comp_overnight_cost_per_mw","h2stor_comp_fixed_om_cost_per_mw_yr","h2stor_comp_mwh_per_kg")

    # Units for BuildH2StorageCompressors are MW of H2
    mod.BuildH2StorageCompressors = Var(
        mod.H2_STORAGE_BLD_YRS,
        within=NonNegativeReals,
        bounds=(0, None)
    )

    mod.H2StorageCompressorCapacity = Expression(
        mod.H2_STORAGE_PROJECTS,
        mod.PERIODS,
        rule=lambda m, s, period: sum(
            m.BuildH2StorageCompressors[s, bld_yr]
            for bld_yr in m.BLD_YRS_FOR_H2_STORAGE_PERIOD[s, period]
        ),
    )

    # Summarize capital costs of H2 storage compressors for the objective function
    mod.H2StorageCompressorsFixedCost = Expression(
        mod.PERIODS,
        rule=lambda m, p: sum(
            sum(
                m.BuildH2StorageCompressors[s, bld_yr]
                * m.h2stor_comp_overnight_cost_per_mw[m.h2stor_type[s]]
                * crf(m.interest_rate, m.h2stor_comp_life_years[m.h2stor_type[s]])
                + m.BuildH2StorageCompressors[s, bld_yr] * m.h2stor_comp_fixed_om_cost_per_mw_yr[m.h2stor_type[s]]
                for bld_yr in m.BLD_YRS_FOR_H2_STORAGE_PERIOD[s, p]
            )
            for s in m.H2_STORAGE_PROJECTS
        ),
    )
    mod.Cost_Components_Per_Period.append("H2StorageCompressorsFixedCost")

    mod.FillH2Storage = Var(mod.H2_STORAGE_TPS, within=NonNegativeReals)

    def Fill_H2_Storage_Upper_Limit_rule(m, s, t):
        return (
            m.FillH2Storage[s, t]
            <= m.H2StorageCompressorCapacity[s, m.tp_period[t]]
        )
    mod.Fill_H2_Storage_Upper_Limit = Constraint(
        mod.H2_STORAGE_TPS, rule=Fill_H2_Storage_Upper_Limit_rule
    )

    # Summarize H2 storage filling for the H2 balance equations
    # (sum for a zone, not a net quantity for a project)
    def rule_f(m, z, t):
        # Construct and cache a set for summation as needed
        if not hasattr(m, "H2_Storage_Fill_Summation_dict"):
            m.H2_Storage_Fill_Summation_dict = collections.defaultdict(set)
            for s, t2 in m.H2_STORAGE_TPS:
                z2 = m.h2stor_load_zone[s]
                m.H2_Storage_Fill_Summation_dict[z2, t2].add(s)
        # Use pop to free memory
        relevant_projects_f = m.H2_Storage_Fill_Summation_dict.pop((z, t), {})
        return sum(m.FillH2Storage[s, t] for s in relevant_projects_f)

    mod.H2StorageTotalFill = Expression(mod.LOAD_ZONES, mod.TIMEPOINTS, rule=rule_f)

    # Register net filling with zonal energy balance. 
    mod.Zone_H2_Withdrawals.append("H2StorageTotalFill")

    mod.WithdrawH2Storage = Var(mod.H2_STORAGE_TPS, within=NonNegativeReals)

    def Withdraw_H2_Storage_Upper_Limit_rule(m, s, t):
		
		# Fraction-of-storage-per-day limit for this storage's technology
		# Units: [kg of H2] * [fraction of capacity/day] * [1 day/24 h]
		#         * [33.32 kWh/kg] * [1 MWh/1000 kWh] = [MW of H2]
        daily_limit = (
            m.H2StorageCapacity[s, m.tp_period[t]] 
            * m.h2stor_cap_frac_withdraw_limit[m.h2stor_type[s]] 
            * 33.32 / (1000 * 24)
        )
		# Constraint: withdraw ≤ min(compressor capacity, fraction-of-storage limit)
        return m.WithdrawH2Storage[s, t] <= min(
			m.H2StorageCompressorCapacity[s, m.tp_period[t]],
			daily_limit
		)  
    mod.Withdraw_H2_Storage_Upper_Limit = Constraint(
        mod.H2_STORAGE_TPS, rule=Withdraw_H2_Storage_Upper_Limit_rule
    )

    # Summarize H2 storage withdrawing for the H2 balance equations
    # (sum for a zone, not a net quantity for a project)
    def rule_w(m, z, t):
        # Construct and cache a set for summation as needed
        if not hasattr(m, "H2_Storage_Withdraw_Summation_dict"):
            m.H2_Storage_Withdraw_Summation_dict = collections.defaultdict(set)
            for s, t2 in m.H2_STORAGE_TPS:
                z2 = m.h2stor_load_zone[s]
                m.H2_Storage_Withdraw_Summation_dict[z2, t2].add(s)
        # Use pop to free memory
        relevant_projects_w = m.H2_Storage_Withdraw_Summation_dict.pop((z, t), {})
        return sum(m.WithdrawH2Storage[s, t]*(1-m.h2stor_leakage_rate[s]) for s in relevant_projects_w)

    mod.H2StorageTotalWithdrawal = Expression(mod.LOAD_ZONES, mod.TIMEPOINTS, rule=rule_w)
    # Register net withdrawal with zonal energy balance. 
    mod.Zone_H2_Injections.append("H2StorageTotalWithdrawal")

    # Only used to improve the performance of calculating H2StorageCompressorLoad
    mod.H2_STORAGE_FOR_ZONE_TPS = Set(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        ordered=False,
        initialize=lambda m, z, t: set(s for s in m.H2_STORAGE_IN_ZONE[z] if (s, t) in m.H2_STORAGE_TPS)
    )

    # Summarize electricity consumption from H2 storage compressors for the power balance equation
    mod.H2StorageCompressorLoad = Expression(
        mod.LOAD_ZONES, mod.TIMEPOINTS,
        rule=lambda m, z, t: \
            sum((m.WithdrawH2Storage[s, t] + m.FillH2Storage[s, t]) * m.h2stor_comp_mwh_per_kg[m.h2stor_type[s]] * (1000/33.32) for s in m.H2_STORAGE_FOR_ZONE_TPS[z, t]),
        doc=("[MW] Average power used at each TP in each zone by H2 storage compressors."))
    mod.Zone_Power_Injections.append('H2StorageCompressorLoad')

    # Summarize hydrogen leakage in storage
    # (sum for a zone)
    def rule_l(m, z, t):
        # Construct and cache a set for summation as needed
        if not hasattr(m, "H2_Storage_Leakage_Summation_dict"):
            m.H2_Storage_Leakage_Summation_dict = collections.defaultdict(set)
            for s, t2 in m.H2_STORAGE_TPS:
                z2 = m.h2stor_load_zone[s]
                m.H2_Storage_Leakage_Summation_dict[z2, t2].add(s)
        # Use pop to free memory
        relevant_projects_l = m.H2_Storage_Leakage_Summation_dict.pop((z, t), {})
        return sum(m.WithdrawH2Storage[s, t]*(m.h2stor_leakage_rate[s]) for s in relevant_projects_l)

    mod.H2Storage_Zonal_H2_Leakage = Expression(mod.LOAD_ZONES, mod.TIMEPOINTS, rule=rule_l)
    # Annual leakage of H2 (fugitive H2 emissions) in each period
	# Units: [MW of H2] * [hours] * [1 kg of H2/33.32 kWh] * [1000 kWh/1 MWh] * [1 metric ton/1000 kg] = [metric ton of H2]
	# 1000/1000 cancels, hence (1/33.32)
    def total_stor_leakage_rule(m, p):
        return sum(
			m.H2Storage_Zonal_H2_Leakage[z, t] * m.tp_weight_in_year[t] * (1/33.32)
			for z in m.LOAD_ZONES for t in m.TPS_IN_PERIOD[p]
		)
    mod.H2StorageTotalLeakage = Expression(mod.PERIODS, rule=total_stor_leakage_rule)
    # Keep track of fugitive H2 emissions in each part of the H2 system in metric tons of kg
    mod.Period_Fugitive_H2.append("H2StorageTotalLeakage")

    mod.H2StateOfFill = Var(mod.H2_STORAGE_TPS, within=NonNegativeReals)

    mod.H2StorageFlow = Expression(
        mod.H2_STORAGE_TPS,
        rule=lambda m, s, t: m.FillH2Storage[s, t]
        - m.WithdrawH2Storage[s, t]
    )

    def H2_Track_State_Of_Fill_rule(m, s, t):
		# Carry-over is just previous fill level (no decay over time like batteries)
        carry_over_h2 = m.H2StateOfFill[s, m.h2_tp_previous[t]]
	
		# Net storage change: net fill level in kg of H2
		# Units: [MW of H2] * [hours] * [1 kg of H2/33.32 kWh] * [1000 kWh/1 MWh] = [kg of H2]
        net_flow = m.H2StorageFlow[s, t] * m.hgts_duration_of_tp[m.tp_to_hgts[t]] * (1000/33.32)
	
        return m.H2StateOfFill[s, t] == carry_over_h2 + net_flow
	
    mod.H2_Track_State_Of_Fill = Constraint(
		mod.H2_STORAGE_TPS, rule=H2_Track_State_Of_Fill_rule
	)

    def H2_State_Of_Fill_Upper_Limit_rule(m, s, t):
        return m.H2StateOfFill[s, t] <= m.H2StorageCapacity[s, m.tp_period[t]]

    mod.H2_State_Of_Fill_Upper_Limit = Constraint(
		mod.H2_STORAGE_TPS, rule=H2_State_Of_Fill_Upper_Limit_rule
	)

    # some H2 storage techs can only complete the specified number of cycles per year, averaged over each period
    # (switch period, not hydrogen period, since the number of cycles is defined per year)
    # Units: [MW of H2] * [hours] * [1 kg of H2/33.32 kWh] * [1000 kWh/1 MWh] = [kg of H2]
    mod.H2_Storage_Cycle_Limit = Constraint(
        mod.H2_STORAGE_PERIODS,
        rule=lambda m, s, p:
        # solvers sometimes perform badly with infinite constraint
        Constraint.Skip
        if m.h2stor_max_cycles_per_year[s] == float("inf")
        else (
            sum(
                m.WithdrawH2Storage[s, tp] * m.tp_duration_hrs[tp] * (1000/33.32)
                for hgts in m.HGTS_IN_PERIOD[p] for tp in m.TPS_IN_HGTS[hgts]
            )
            <= m.h2stor_max_cycles_per_year[s]
            * m.H2StorageCapacity[s, p]
            * m.period_length_years[p]
        ),
    )
    
    mod.H2_Storage_Conservation_of_Mass_Annual = Constraint(
        mod.LOAD_ZONES, 
        mod.PERIODS, 
        rule=lambda m, z, p:
        sum(
            (m.FillH2Storage[z, tp] - m.WithdrawH2Storage[z, tp])
            for hgts in m.HGTS_IN_PERIOD[p] 
            for tp in m.TPS_IN_HGTS[hgts] 
        ) == 0
    )

def load_inputs(m, switch_data, inputs_dir):
    # Construct set of capacity-limited projects. This set includes projects for 
    # which the prod_capacity_limit_mw parameter has a value.
    # Note we removed the capability to have discretely sized H2 production techs
    if 'h2stor_maximum_size_kg' in switch_data.data():
        switch_data.data()['CAPACITY_LIMITED_H2_STORAGE'] = {
            None: list(switch_data.data(name='h2stor_maximum_size_kg').keys())}
        
def post_solve(instance, outdir):
    """
    Export H2 storage build information to h2_storage_builds.csv,
    H2 storage capacity to h2_storage_capacity.csv, and H2 storage
    dispatch info to h2_storage_dispatch.csv
    """

    # Write how much is built each build year for each project to h2_storage_builds.csv
    write_table(
        instance,
        instance.H2_STORAGE_BLD_YRS,
        output_file=os.path.join(outdir, "h2_storage_builds.csv"),
        headings=(
            "h2_storage_project",
            "build_year",
            "load_zone",
            "IncrementalCapacitykgH2",
        ),
        values=lambda m, s, bld_yr: (
            s,
            bld_yr,
            m.gen_load_zone[s],
            m.BuildH2Storage[s, bld_yr],
        ),
    )
    # Write the total capacity for each project at each period to h2_storage_capacity.csv
    write_table(
        instance,
        instance.H2_STORAGE_PERIODS,
        output_file=os.path.join(outdir, "h2_storage_capacity.csv"),
        headings=(
            "h2_storage_project",
            "period",
            "load_zone",
            "OnlineCapacitykgH2",
        ),
        values=lambda m, s, p: (
            s,
            p,
            m.gen_load_zone[s],
            m.H2StorageCapacity[s, p],
        ),
    )
    # Write how much is dispatched by each project at each time point to storage_dispatch.csv
    write_table(
        instance,
        instance.H2_STORAGE_TPS,
        output_file=os.path.join(outdir, "h2_storage_dispatch.csv"),
        # TODO renaming heading to timestamp (and update graphing accordingly)
        headings=(
            "h2_storage_project",
            "timepoint",
            "load_zone",
            "ChargeMW",
            "DischargeMW",
            "H2StateOfFill",
        ),
        values=lambda m, s, t: (
            s,
            m.tp_timestamp[t],
            m.gen_load_zone[s],
            m.FillH2Storage_kg_per_hr[s, t],
            m.WithdrawH2Storage_kg_per_hr[s, t],
            m.H2StateOfFill[s, t],
        ),
    )