# Cowling Trading Co catalog

Public own-shop catalog, generated from https://www.etsy.com/shop/CowlingTradingCo/rss. Checkout, current prices, options and delivery estimates remain on Etsy.

GitHub Actions refreshes it every six hours and deploys the generated site directly. Its short-lived built-in GitHub token is scoped to this repository; no Etsy, Printify or Pinterest credential is used. Source errors stop deployment and leave the last successful site intact. Workflow failures appear in Actions and follow the owner's GitHub notification settings. `status.json` records the last successful refresh.

Pinterest's native RSS auto-publisher reads `https://lance19c.github.io/feed.xml` and saves to the account's New Home Gifts board. Website ownership was verified with the owner's explicitly approved public HTML tag. The application does not call Pinterest's create-Pin API or automate the Pinterest UI to create Pins.

The feed exposes at most two new product IDs per UTC day. Stable GUIDs and committed state prevent daily reposts. The source feed contains Etsy's ten newest products, so this is ongoing new-product promotion, not a complete historical listing import. Product pages remain available when they leave that ten-product window, with checkout pointing to Etsy for current availability.

Legacy high-shipping ornaments are excluded from the promotion feed. `quality-policy.json` can admit ornaments created after the cheaper supplier mapping is deployed. Existing old products are not silently migrated to another physical product or supplier.

Run `python -m unittest discover -s scripts -p test_build.py` and `python scripts/build.py` to validate and regenerate. New source and public-output files must be explicitly selected for commits. Never add credentials or private customer data.
