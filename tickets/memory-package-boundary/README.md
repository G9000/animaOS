# Memory Package Boundary Tickets

This folder tracks follow-up work after SUM-011 and SUM-005. SUM-011 created the initial `anima_server.services.memory` package boundary; this initiative hardens that boundary into the stable ownership surface for future memory work.

The goal is not to rewrite memory in one pass. Each ticket moves one slice behind `services.memory` while preserving existing `services.agent` compatibility.
