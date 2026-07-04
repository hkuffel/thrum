"""Transport projections of Operations onto synchronous surfaces (ADR-0028). Each
projection decodes a transport's inputs, runs the Operation inline through the
shared Execution Scope, and encodes the output — independent of any specific
Operation."""
