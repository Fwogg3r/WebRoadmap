---
title: Jacobi PBD contacts
nickname: PBD
status: done
links: [phase2/a]
---
**What:** position-based contact with friction.

**Why:** stable at 60 Hz with large time steps.

**How:** each particle computes only its own correction (gather), averaged by contact count.
