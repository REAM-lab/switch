# Copyright (c) 2015-2019 The Switch Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0, which is in the LICENSE file.

"""
Defines hydrogen transport (pipelines) build-outs.

INPUT FILE FORMAT
    Import data related to H2 pipeline builds. The following files are
    expected in the input directory:

    h2_pipelines.csv
        PIPELINE_ID, pip_lz1, pip_lz2, pip_length_km, pip_efficiency,
        existing_pip_cap_gw_h2, pip_terrain_multiplier
    The last column of h2_pipelines.csv is optional. If the
    column is missing or if cells contain a dot (.), pip_terrain_multiplier
    will be set to default value as described in documentation.

    Note that in the next file, parameter names are written on the first
    row (as usual), and the single value for each parameter is written in
    the second row.

    h2_pipeline_params.csv
        pip_capital_cost_per_m_per_gw_h2, pip_capital_cost_y_int, 
        pip_lifetime_yrs, pip_fixed_om_per_m
"""

import os
from pyomo.environ import *
from switch_model.financials import capital_recovery_factor as crf
import pandas as pd
from switch_model.reporting import write_table

dependencies = 'switch_model.timescales', 'switch_model.hydrogen.h2_advanced.h2_timescales',\
    'switch_model.financials','switch_model.balancing.load_zones',

def define_components(mod):
    if not mod.options.no_hydrogen:
        define_hydrogen_components(mod)

def define_hydrogen_components(mod):
    """

    Adds components to a Pyomo abstract model object to describe bulk
    H2 pipelines of an H2 system. This includes parameters, build
    decisions and constraints. Unless otherwise stated, all pipeline
    capacity is specified in units of MW of hydrogen and all sets and parameters 
    are mandatory. MW of hydrogen is a measure of the pipeline's maximum hydrogen 
    flow in kg of H2 per s, converted to MW using the LHV 
    of H2 of 33.32 kWh/kg from https://www.engineeringtoolbox.com/fuels-higher-calorific-values-d_169.html
    and https://sci-hub.kvnp.top/10.1016/j.ijhydene.2019.10.080. 
    (Example: Say a pipeline is rated for 8.34 kg of H2 per s. Then we have:
    8.34 kg_H2/s * 33.32 kWh/kg * 1 MW/1,000 kW * 3,600 s/hr =~ 1,000 MW of H2 or 1 GW of H2)

    PIPELINES is the complete set of transmission pathways
    connecting load zones. Each member of this set is a one dimensional
    identifier such as "A-B". This set has no regard for directionality
    of transmission lines and will generate an error if you specify two
    lines that move in opposite directions such as (A to B) and (B to
    A). Another derived set - PIP_LINES_DIRECTIONAL - stores
    directional information. Transmission may be abbreviated as trans or
    pip in parameter names or indexes.

    pip_lz1[pip] and pip_lz2[pip] specify the load zones at either end
    of a transmission line. The order of 1 and 2 is unimportant, but you
    are encouraged to be consistent to simplify merging information back
    into external databases.

    pip_dbid[pip in PIPELINES] is an external database
    identifier for each transmission line. This is an optional parameter
    than defaults to the identifier of the transmission line.

    pip_length_km[pip in PIPELINES] is the length of each
    transmission line in kilometers.

    pip_efficiency[pip in PIPELINES] is the proportion of
    energy sent down a line that is delivered. If 2 percent of energy
    sent down a line is lost, this value would be set to 0.98.

    BLD_YRS_FOR_PIP is the set of transmission lines and years in
    which they have been or could be built. This set includes past and
    potential future builds. All future builds must come online in the
    first year of an investment period. This set is composed of two
    elements with members:  (pip, build_year). For existing transmission
    where the build years are not known, build_year is set to 'Legacy'.

    BLD_YRS_FOR_EXISTING _PIP is a subset of BLD_YRS_FOR_PIP that lists
    builds that happened before the first investment period. For most
    datasets the build year is unknown, so is it always set to 'Legacy'.

    existing_pip_cap[pip in PIPELINES] is a parameter that
    describes how many MW of capacity has been installed before the
    start of the study.

    NEW_PIP_BLD_YRS is a subset of BLD_YRS_FOR_PIP that describes
    potential builds.

    BuildPip[ (pip, bld_yr) in BLD_YRS_FOR_PIP] is a decision variable
    that describes the transfer capacity in MW installed on a corridor
    in a given build year. For existing builds, this variable is locked
    to the existing capacity.

    PipCapacityNameplate[ (pip, bld_yr) in BLD_YRS_FOR_PIP] is an expression
    that returns the total nameplate transfer capacity of a transmission
    line in a given period. This is the sum of existing and newly-build
    capacity.

    pip_derating_factor[pip in PIPELINES] is an overall
    derating factor for each transmission line that can reflect forced
    outage rates, stability or contingency limitations. This parameter
    is optional and defaults to 1. This parameter should be in the
    range of 0 to 1, being 0 a value that disables the line completely.

    PipCapacityNameplateAvailable[ (pip, bld_yr) in BLD_YRS_FOR_PIP] is an
    expression that returns the available transfer capacity of a
    transmission line in a given period, taking into account the
    nameplate capacity and derating factor.

    pip_terrain_multiplier[pip in PIPELINES] is
    a cost adjuster applied to each transmission line that reflects the
    additional costs that may be incurred for traversing that specific
    terrain. Crossing mountains or cities will be more expensive than
    crossing plains. This parameter is optional and defaults to 1. This
    parameter should be in the range of 0.5 to 3.

    pip_capital_cost_per_mw_km describes the generic costs of building
    new transmission in units of $BASE_YEAR per MW transfer capacity per
    km. This is optional and defaults to 1000.

    pip_lifetime_yrs is the number of years in which a capital
    construction loan for a new transmission line is repaid. This
    optional parameter defaults to 20 years based on 2009 WREZ
    transmission model transmission data. At the end of this time,
    we assume transmission lines will be rebuilt at the same cost.

    pip_fixed_om_fraction describes the fixed Operations and
    Maintenance costs as a fraction of capital costs. This optional
    parameter defaults to 0.03 based on 2009 WREZ transmission model
    transmission data costs for existing transmission maintenance.

<<<<<<< HEAD
    pip_cost_hourly[PIPELINES] is the cost of building
=======
    pip_cost_hourly[tx PIPELINES] is the cost of building
>>>>>>> 9f8376e (inital changes to pipeline transport module)
    transmission lines in units of $BASE_YEAR / MW- transfer-capacity /
    hour. This derived parameter is based on the total annualized
    capital and fixed O&M costs, then divides that by hours per year to
    determine the portion of costs incurred hourly.

    DIRECTIONAL _PIP is a derived set of directional paths that
    electricity can flow along transmission lines. Each element of this
    set is a two-dimensional entry that describes the origin and
    destination of the flow: (load_zone_from, load_zone_to). Every
    transmission line will generate two entries in this set. Members of
    this set are abbreviated as pip_d where possible, but may be
    abbreviated as pip in situations where brevity is important and it is
    unlikely to be confused with the overall transmission line.

    pip_d_line[pip_d] is the transmission line associated with this
    directional path.

<<<<<<< HEAD
    PIP_BUILDS_IN_PERIOD[p in PERIODS] is an indexed set that
=======
    TX_BUILDS_IN_PERIOD[p in PERIODS] is an indexed set that
>>>>>>> 9f8376e (inital changes to pipeline transport module)
    describes which transmission builds will be operational in a given
    period. Currently, transmission lines are kept online indefinitely,
    with parts being replaced as they wear out.

<<<<<<< HEAD
    PIP_BUILDS_IN_PERIOD[p] will return a subset of  (pip, bld_yr)
=======
    TX_BUILDS_IN_PERIOD[p] will return a subset of  (pip, bld_yr)
>>>>>>> 9f8376e (inital changes to pipeline transport module)
    in BLD_YRS_FOR_PIP.

    --- Delayed implementation ---

    is_dc_line ... Do I even need to implement this?

    --- NOTES ---

    The cost stream over time for transmission lines differs from the
    Switch-WECC model. The Switch-WECC model assumed new transmission
    had a financial lifetime of 20 years, which was the length of the
    loan term. During this time, fixed operations & maintenance costs
    were also incurred annually and these were estimated to be 3 percent
    of the initial capital costs. These fixed O&M costs were obtained
    from the 2009 WREZ transmission model transmission data costs for
    existing transmission maintenance .. most of those lines were old
    and their capital loans had been paid off, so the O&M were the costs
    of keeping them operational. Switch-WECC basically assumed the lines
    could be kept online indefinitely with that O&M budget, with
    components of the lines being replaced as needed. This payment
    schedule and lifetimes was assumed to hold for both existing and new
    lines. This made the annual costs change over time, which could
    create edge effects near the end of the study period. Switch-WECC
    had different cost assumptions for local T&D; capital expenses and
    fixed O&M expenses were rolled in together, and those were assumed
    to continue indefinitely. This basically assumed that local T&D would
    be replaced at the end of its financial lifetime.

    Switch treats all transmission and distribution (long-
    distance or local) the same. Any capacity that is built will be kept
    online indefinitely. At the end of its financial lifetime, existing
    capacity will be retired and rebuilt, so the annual cost of a line
    upgrade will remain constant in every future year.

    """

    mod.PIPELINES = Set(dimen=1, input_file="h2_pipelines.csv")
    mod.pip_lz1 = Param(mod.PIPELINES, within=mod.LOAD_ZONES, input_file="h2_pipelines.csv")
    mod.pip_lz2 = Param(mod.PIPELINES, within=mod.LOAD_ZONES, input_file="h2_pipelines.csv")
    # we don't do a min_data_check for PIPELINES, because it may be empty for model
    # configurations that are sometimes run with interzonal transmission and sometimes not
    # (e.g., island interconnect scenarios). However, presence of this column will still be
    # checked by load_data_aug.
    mod.min_data_check('pip_lz1', 'pip_lz2')
<<<<<<< HEAD
    mod.pip_dbid = Param(mod.PIPELINES, default=lambda m, pip:: pip, within=Any, input_file="h2_pipelines.csv")
=======
    mod.pip_dbid = Param(mod.PIPELINES, default=lambda m, tx: pip, within=Any, input_file="h2_pipelines.csv")
>>>>>>> 9f8376e (inital changes to pipeline transport module)
    mod.pip_length_km = Param(mod.PIPELINES, within=NonNegativeReals, input_file="h2_pipelines.csv")
    mod.pip_efficiency = Param(
        mod.PIPELINES,
        within=PercentFraction, input_file="h2_pipelines.csv")
    mod.existing_pip_cap = Param(
        mod.PIPELINES,
        within=NonNegativeReals, input_file="h2_pipelines.csv")
    mod.min_data_check(
        'pip_length_km', 'pip_efficiency', 'existing_pip_cap')
    mod.pip_new_build_allowed = Param(
        mod.PIPELINES, within=Boolean, default=True, input_file="h2_pipelines.csv")
    mod.pip_capital_cost_per_mw_km = Param(
        within=NonNegativeReals,
        default=1000, input_file="h2_pipeline_params.csv")
    mod.PIP_BLD_YRS = Set(
        dimen=2,
        initialize=mod.PIPELINES * mod.PERIODS,
        filter=lambda m, pip, p: m.pip_new_build_allowed[pip] and m.pip_capital_cost_per_mw_km != float("inf"))
    mod.BuildPip = Var(mod.PIP_BLD_YRS, within=NonNegativeReals)
    mod.NewPipCapacity = Expression(
        mod.PIPELINES, mod.PERIODS,
        rule=lambda m, pip, period: sum(
            m.BuildPip[pip, bld_yr]
            for bld_yr in m.PERIODS
            if bld_yr <= period and  (pip, bld_yr) in m.PIP_BLD_YRS
        )
    )
    mod.PipCapacityNameplate = Expression(
        mod.PIPELINES, mod.PERIODS,
        rule=lambda m, pip, p: m.NewPipCapacity[pip, p] + m.existing_pip_cap[pip])
    mod.pip_derating_factor = Param(
        mod.PIPELINES,
        within=PercentFraction,
        default=1, input_file="h2_pipelines.csv")
    mod.PipCapacityNameplateAvailable = Expression(
        mod.PIPELINES, mod.PERIODS,
        rule=lambda m, pip, period: (
            m.PipCapacityNameplate[pip, period] * m.pip_derating_factor[pip]))
    mod.pip_terrain_multiplier = Param(
        mod.PIPELINES,
        within=NonNegativeReals,
        default=1, input_file="h2_pipelines.csv")
    mod.pip_lifetime_yrs = Param(
        within=NonNegativeReals,
        default=20, input_file="h2_pipeline_params.csv")
    mod.pip_fixed_om_fraction = Param(
        within=NonNegativeReals,
        default=0.03, input_file="h2_pipeline_params.csv")
    # Total annual fixed costs for building new transmission lines...
    # Multiply capital costs by capital recover factor to get annual
    # payments. Add annual fixed O&M that are expressed as a fraction of
    # overnight costs.
    mod.pip_cost_annual = Param(
        mod.PIPELINES,
        within=NonNegativeReals,
<<<<<<< HEAD
        initialize=lambda m, pip: (
=======
        initialize=lambda m, tx: (
>>>>>>> 9f8376e (inital changes to pipeline transport module)
            m.pip_capital_cost_per_mw_km * m.pip_terrain_multiplier[pip] *
            m.pip_length_km[pip] * (crf(m.interest_rate, m.pip_lifetime_yrs) +
                m.pip_fixed_om_fraction)))
    # An expression to summarize annual costs for the objective
    # function. Units should be total annual future costs in $base_year
    # real dollars. The objective function will convert these to
    # base_year Net Present Value in $base_year real dollars.
    mod.PipelineCosts = Expression(
        mod.PIPELINES, mod.PERIODS,
        rule=lambda m, pip, p: m.NewPipCapacity[pip, p] * m.pip_cost_annual[pip] if  (pip, p) in m.PIP_BLD_YRS else 0
    )
    mod.PipFixedCosts = Expression(
        mod.PERIODS,
        rule=lambda m, p: sum(
            m.PipelineCosts[pip, p] for pip in m.PIPELINES
        )
    )
    mod.Cost_Components_Per_Period.append('PipFixedCosts')

    def init_DIRECTIONAL _PIP(model):
<<<<<<< HEAD
        pip_dir = set()
        for pip in model.PIPELINES:
            pip_dir.add((model.pip_lz1[pip], model.pip_lz2[pip]))
            pip_dir.add((model.pip_lz2[pip], model.pip_lz1[pip]))
        return pip_dir
    mod.DIRECTIONAL _PIP = Set(
        dimen=2,
        initialize=init_DIRECTIONAL _PIP)
    mod.PIP_CONNECTIONS_TO_ZONE = Set(
=======
        tx_dir = set()
        for pip in model.PIPELINES:
            tx_dir.add((model.pip_lz1[pip], model.pip_lz2[pip]))
            tx_dir.add((model.pip_lz2[pip], model.pip_lz1[pip]))
        return tx_dir
    mod.DIRECTIONAL _PIP = Set(
        dimen=2,
        initialize=init_DIRECTIONAL _PIP)
    mod.TX_CONNECTIONS_TO_ZONE = Set(
>>>>>>> 9f8376e (inital changes to pipeline transport module)
        mod.LOAD_ZONES,
        ordered=False,
        initialize=lambda m, lz: set(
            z for z in m.LOAD_ZONES if (z,lz) in m.DIRECTIONAL _PIP))

    def init_pip_d_line(m, zone_from, zone_to):
        for pip in m.PIPELINES:
            if((m.pip_lz1[pip] == zone_from and m.pip_lz2[pip] == zone_to) or
               (m.pip_lz2[pip] == zone_from and m.pip_lz1[pip] == zone_to)):
                return tx
    mod.pip_d_line = Param(
        mod.DIRECTIONAL _PIP,
        within=mod.PIPELINES,
        initialize=init_pip_d_line)


def post_solve(instance, outdir):
    mod = instance
<<<<<<< HEAD
    pip_build_df = pd.DataFrame([
=======
    tx_build_df = pd.DataFrame([
>>>>>>> 9f8376e (inital changes to pipeline transport module)
        {
            "TRANSMISSION_LINE": pip,
            "PERIOD": p,
            "pip_lz1": mod.pip_lz1[pip],
            "pip_lz2": mod.pip_lz2[pip],
            "pip_dbid": mod.pip_dbid[pip],
            "pip_length_km": mod.pip_length_km[pip],
            "pip_efficiency": mod.pip_efficiency[pip],
            "pip_derating_factor": mod.pip_derating_factor[pip],
            "existing_pip_cap": mod.existing_pip_cap[pip],
            "BuildPip": value(mod.BuildPip[pip, p]) if  (pip, p) in mod.BuildPip else ".",
            "PipCapacityNameplate": value(mod.PipCapacityNameplate[pip, p]),
            "PipCapacityNameplateAvailable": value(mod.PipCapacityNameplateAvailable[pip, p]),
            "TotalAnnualCost": value(mod.PipelineCosts[pip, p])
        } for pip, p in mod.PIPELINES * mod.PERIODS
    ])
<<<<<<< HEAD
    pip_build_df.set_index(["TRANSMISSION_LINE", "PERIOD"], inplace=True)
    write_table(instance, df=pip_build_df, output_file=os.path.join(outdir, "transmission.csv"))
=======
    tx_build_df.set_index(["TRANSMISSION_LINE", "PERIOD"], inplace=True)
    write_table(instance, df=tx_build_df, output_file=os.path.join(outdir, "transmission.csv"))
>>>>>>> 9f8376e (inital changes to pipeline transport module)

@graph(
    "transmission_capacity",
    title="Transmission capacity per period"
)
def transmission_capacity(tools):
    transmission = tools.get_dataframe("transmission.csv", convert_dot_to_na=True).fillna(0)
    transmission = transmission.groupby("PERIOD", as_index=False).sum()
    transmission["Existing Capacity"] = transmission["PipCapacityNameplate"] - transmission["BuildPip"]
    transmission = transmission[["PERIOD", "Existing Capacity", "BuildPip"]]
    transmission = transmission.set_index("PERIOD")
    transmission = transmission.rename({"BuildPip": "New Capacity"}, axis=1)
    transmission *= 1e-3  # Convert to GW

    transmission.plot(
        kind='bar',
        stacked=True,
        ax=tools.get_axes(),
        xlabel="Period",
        ylabel="Transmission capacity (GW)"
    )
    tools.bar_label()


@graph(
    "transmission_map",
    title="Total transmission capacity for the last period (in GW)",
    note="Lines <1 GW not shown"
)
def transmission_map(tools):
    if not tools.maps.can_make_maps():
        return
    transmission = tools.get_dataframe("transmission.csv", convert_dot_to_na=True).fillna(0)
    # Keep only the last period
    last_period = transmission["PERIOD"].max()
    transmission = transmission[transmission["PERIOD"] == last_period].drop("PERIOD", axis=1)
    # Rename the columns appropriately
    transmission = transmission.rename({"pip_lz1": "from", "pip_lz2": "to", "PipCapacityNameplate": "value"}, axis=1)
    transmission = transmission[["from", "to", "value"]]
    transmission.value *= 1e-3
    tools.maps.graph_transmission_capacity(transmission)

@graph(
    "transmission_buildout",
    title="New transmission capacity built across all periods (in GW)",
    note="Lines with <0.1 GW built not shown."
)
def transmission_map(tools):
    if not tools.maps.can_make_maps():
        return
    transmission = tools.get_dataframe("transmission.csv", convert_dot_to_na=True).fillna(0)
    transmission = transmission.rename({"pip_lz1": "from", "pip_lz2": "to", "BuildPip": "value"}, axis=1)
    transmission = transmission[["from", "to", "value", "PERIOD"]]
    transmission = transmission.groupby(["from", "to", "PERIOD"], as_index=False).sum().drop("PERIOD", axis=1)
    # Rename the columns appropriately
    transmission.value *= 1e-3
    tools.maps.graph_transmission_capacity(transmission)