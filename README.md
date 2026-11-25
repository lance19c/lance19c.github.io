# Cowling Trading Co catalog

Public own-shop catalog, generated from https://www.etsy.com/shop/CowlingTradingCo/rss. Checkout, current prices, options and delivery estimates remain on Etsy.

GitHub Actions refreshes it every six hours and deploys the generated site directly. Its short-lived built-in GitHub token is scoped to this repository; no Etsy, Printify or Pinterest credential is used. Source errors stop deployment and leave the last successful site intact. Workflow failures appear in Actions and follow the owner's GitHub notification settings. `status.json` records the last successful refresh.

Pinterest's native RSS auto-publisher reads `https://lance19c.github.io/feed.xml` and saves to the account's New Home Gifts board. Website ownership was verified with the owner's explicitly approved public HTML tag. The application does not call Pinterest's create-Pin API or automate the Pinterest UI to create Pins.

The feed exposes at most two new product IDs per UTC day. Stable GUIDs and committed state prevent daily reposts. The public Etsy RSS source contains only the ten newest products. Optional `inventory-snapshot.json` can queue eligible products that are already active, without creating listings or paying listing fees. The snapshot is accepted only for the pinned own-shop identity and a complete allowlisted read. New historical introductions require at least three verified public images and stop seven days after the inventory observation. A renewed listing date never replaces original creation for the ornament cutoff. Existing GUIDs/timestamps are retained across refreshes. Product pages remain available when they leave that ten-product window, with checkout pointing to Etsy for current availability.

Legacy high-shipping ornaments are excluded from the promotion feed. `quality-policy.json` can admit ornaments created after the cheaper supplier mapping is deployed. Existing old products are not silently migrated to another physical product or supplier.

A one-time inventory seed is **not continuous inventory synchronization**. `status.json` exposes its original observation, expiry and freshness separately from the public RSS refresh. `inventory_refresh_connected` stays false: no unattended inventory publishing capability is installed or enabled by this change. A reviewed daily server exporter plus a separate protected repository-only publishing capability is required for ongoing full-inventory freshness; that optional operational proposal remains OFF until separately provisioned. The existing six-hour catalog job itself does not depend on a laptop.

The complete 75-ID quality blocklist and ornament cutoff still apply to both catalog cards and feed items. Low-image historical products remain held; existing public RSS promotion behavior remains unchanged. Digital downloads are labeled as digital, without physical-printing or shipping claims. Current price, availability and checkout always remain on Etsy.

Run `python -m unittest discover -s scripts -p 'test_*.py'` and `python scripts/build.py` to validate and regenerate. New source and public-output files must be explicitly selected for commits. Never add credentials or private customer data.

A source snapshot can be validated locally with `python scripts/inventory.py inventory-snapshot.json`. This never contacts a provider. Invalid, foreign, partial, future-dated or malformed source data refuses the build before generated pages or state are replaced. No credentials or customer data belong in this public repository.
