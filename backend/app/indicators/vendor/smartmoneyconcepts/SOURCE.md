# Vendored smart-money-concepts

This directory contains the upstream `smartmoneyconcepts` source used by the
market-structure adapter.

- Upstream: https://github.com/joshyattridge/smart-money-concepts
- Snapshot commit: `1b62fd6c41e1f508e7ed76831a039fa4c82d42f6`
- Upstream package version: `0.0.27`
- License: MIT (see `LICENSE`)

The adapter in `app.indicators.smc_adapter` owns project-specific semantics,
including confirmation dates and API output mapping. Do not call the vendored
module directly from API code.
