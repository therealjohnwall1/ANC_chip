# Noise Canceling Model/Chip design
**about**: active noise canceling chip intended for asic, prototyped on fpga

## Directories
- **scripts**: python scripts to simulate the logic and create golden results for the logic on Chip
- **chip**: everything related to chip logic, synthesis and testing
```
chip/
├── doc/
│   ├── requirements.md
│   ├── architecture.md
│   ├── microarchitecture.md
│   ├── interfaces.md
│   ├── register_map.md
│   └── verification_plan.md
│
├── rtl/
│   ├── chip_top.sv
│   ├── bus/
│   ├── uart/
│   ├── gpio/
│   ├── timer/
│   └── common/
│
├── dv/
│   ├── unit/
│   ├── integration/
│   ├── formal/
│   ├── models/
│   └── testplans/
│
├── sw/
│   ├── drivers/
│   ├── tests/
│   └── boot/
│
├── syn/
│   ├── constraints/
│   └── scripts/
│
└── physical/
    ├── floorplan/
    ├── constraints/
    └── scripts/
```
- **breakout**: breakout board for the breakout board ez
- **drivers**: device drivers for chip/board
