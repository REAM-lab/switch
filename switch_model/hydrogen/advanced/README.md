# Advanced Hydrogen Model as part of SWITCH for REAM

Welcome! This folder contains the SWITCH hydrogen planning model to be used together with the electricity planning model of the REAM research lab.

All five of the following modules must be present to run the advanced hydrogen system expansion model. Copy and paste them into the modules.txt of your runs that include hydrogen (They must be pasted IN THIS ORDER, after switch_model.generators.core.dispatch and before switch_model.reporting):

switch_model.hydrogen.advanced.h2_timescales
switch_model.hydrogen.advanced.h2_production
switch_model.hydrogen.advanced.h2_storage
switch_model.hydrogen.advanced.h2_pipelines
switch_model.hydrogen.advanced.h2_to_power

## Module Descriptions:
h2_timescales:
Users define custom timescales that set the hydrogen storage cycle.

h2_production:
Users use input files to describe hydrogen production projects, associated costs and technology parameters, exogenous hydrogen demand, emissions factors, and hydrogen carbon policies. The build and dispatch of these projects are defined in this module.

h2_storage:
Users use input files to describe hydrogen storage projects and associated costs and technology parameters. The build and dispatch of these projects are defined in this module.

h2_pipelines:
Users use input files to describe hydrogen pipeline projects, associated costs, lengths, and compressor parameters. Pipelines are able to transport H2 from one load zone to another load zone that is connected to it via pipeline. The build and dispatch of these pipelines are defined in this module.

h2_to_power:
Users use input files to describe hydrogen-fueled electricity generation projects, associated costs, and parameters. These projects contribute to both H2 withdrawals (fuel use) and power injections. The build and dispatch of these projects are defined in this module. 