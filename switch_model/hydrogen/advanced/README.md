# Advanced Hydrogen Model as part of SWITCH for REAM

Welcome! This folder contains the SWITCH hydrogen planning model to be used together with the electricity planning model of the REAM research lab.
All of the following modules must be present to run the advanced hydrogen system expansion model. Copy and paste them into the 
modules.txt of your runs that include hydrogen (They must be pasted IN THIS ORDER, after switch_model.generators.core.dispatch and before switch_model.reporting):

switch_model.hydrogen.advanced.h2_timescales
switch_model.hydrogen.advanced.h2_production_build
switch_model.hydrogen.advanced.h2_production_dispatch
switch_model.hydrogen.advanced.h2_storage
switch_model.hydrogen.advanced.h2_pipelines
switch_model.hydrogen.advanced.h2_to_power

## Module Descriptions:
h2_timescales:

h2_production_build:

h2_production_dispatch:

h2_storage:

h2_pipelines:

h2_to_power: