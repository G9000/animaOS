# RX-440 Relay Field Manual

## Overview

The RX-440 industrial relay switches inductive loads up to 40 amperes. A relay that
fails closed can trigger a cascade fault across the checkpoint bus, so inspection
and replacement discipline matter more than raw duty cycle. This manual covers
mounting, wiring, inspection, replacement intervals, fault codes, and storage.

## Installation

### Mounting

Mount the relay on a 35 mm DIN rail inside the control cabinet. Torque the mounting
bolts to 2.4 newton-metres; over-torquing cracks the phenolic base and under-torquing
lets vibration walk the relay off the rail. Leave 15 mm of clearance above the vent
slots for convection cooling.

### Wiring

Use 14 AWG stranded copper conductors for the load circuit and land them on terminal
block T3. Never land aluminum conductors on the T3 block: galvanic corrosion at the
lug raises contact resistance and cooks the terminal. Control-side wiring accepts
18 AWG on the T1 pair. Tug-test every lug after landing.

## Inspection

Open the relay housing quarterly. Check the contact pads for pitting or
discoloration; pitted contacts arc under load and weld shut without warning.
Measure coil resistance with the circuit de-energized and compare it against the
nameplate rating of 55 ohms; a reading above 70 ohms indicates winding fatigue.
A thermal camera pass across the cabinet finds failing relays faster than any
electrical test: look for a coil body more than 15 degrees above its neighbors.

## Replacement Intervals

Replace relays on schedule, not on failure. Intervals assume a resistive load mix;
halve them for motor loads.

| Model | Interval | Notes |
| --- | --- | --- |
| R-100 | 10,000 cycles | Legacy unit, no arc suppression |
| RX-440 | 25,000 cycles | Integrated arc quench |
| RX-500 | 40,000 cycles | Sealed contacts, motor rated |

Log every replacement in the maintenance ledger with the cycle counter reading.

## Fault Codes

The RX-440 status header blinks fault codes on the diagnostic LED.

| Code | Meaning | Action |
| --- | --- | --- |
| E-17 | Coil open circuit | Replace relay; check T1 control fusing |
| E-23 | Contact weld detected | Replace relay immediately; inspect load for shorts |
| E-31 | Thermal overload | Verify vent clearance and cabinet airflow before restart |

Clear a fault by holding the reset stud for five seconds after correcting the cause.

## Storage

Store spare relays in sealed bags with silica gel desiccant. The rated storage range
is -20 to 60 degrees Celsius; humidity above 60 percent corrodes the contact silver
even inside the housing. Rotate spare stock so no unit sits longer than three years.
