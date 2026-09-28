# Contributing

Keep engineering logic deterministic, testable and expressed in SI units. All API data access must be tenant scoped using the authenticated membership—never a client-supplied organization identifier. Recommendations must remain advisory and must identify uncertainty and model-range limitations.

Before a pull request run `make check`. Add a physics validation case for any model-equation change and use time-ordered splits for any ML change. Do not add secrets, captured production plant data, credentials, or direct-control functionality.

