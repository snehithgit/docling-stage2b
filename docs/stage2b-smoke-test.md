# Stage 2B smoke-test record

The development environment cannot reach the private `192.168.68.x` LAN, so no claim is made that the real Pi5 or OnePlus models were invoked from this build environment.

The execution path was smoke-tested against the real routed items from `crane manual.zip` using a local deterministic mock OpenAI-compatible verifier. This validated the data path without faking a LAN result:

- current route import: 8 Pi5 + 4 OnePlus routes
- manual Pi5 Start authorizes exactly the 8 currently waiting Pi5 routes
- OnePlus remains paused until its own Start/Auto Run is enabled
- text route R00004 correctly extracted page 41 suspect text from the raw Docling JSON
- decorative page-10 image resolved from the full image and generated 0 crop calls
- unresolved page-41 technical image generated exactly 4 sequential overlapping crops
- merged vision result accepted technical evidence found in crops
- only one model-discovery health lookup was required per device in the smoke run
- the parsed Docling JSON was cached once for the source ZIP rather than CRC-scanning/reparsing it per route

All real Pi5/OnePlus verdicts must be collected on the user's LAN through the new Quality-page controls. The build intentionally keeps Auto Run off by default.
