Source: https://github.com/day0market/support_resistance
Commit: fdd4a7f0fdccf6c4007da79fd6f3879164462c3c
Original paths: pricelevels/_abstract.py, pricelevels/cluster.py, pricelevels/exceptions.py
Upstream setup.py declares license='wtfpl'; upstream repository has no standalone LICENSE file. Verify redistribution rights before broader distribution.
Adaptation: moved the ZigZag import in _abstract.py into BaseZigZagLevels._find_potential_level_prices, because RawPriceClusterLevels has no need for ZigZag and current Python runtime cannot use upstream pinned ZigZag==0.2.2. No changes to clustering algorithm.
Upstream pinned pandas==0.25.0 and scikit-learn==0.21.2 are not used; this application resolves maintained compatible versions from official PyPI.
