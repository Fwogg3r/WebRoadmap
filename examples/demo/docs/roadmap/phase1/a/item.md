---
title: Counting-sort neighbour grid
nickname: Grid
status: done
links: [phase1/b]
---
**What:** cell-sorted copy of the hot particle data.

**Why:** gather-style solve needs neighbours in cache order.

**How:** `count` → `scan` → `scatter`, one thread per particle.
