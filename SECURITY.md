# Security and deployment boundary

This application is for a trusted private network. It does not provide user
accounts, authentication between trusted LAN devices, or internet-facing TLS.
Any permitted LAN client can access collections. Do not expose the service with
router port forwarding or a public tunnel. Public/wildcard binding is rejected.

The HTTP interface enforces a local origin, image uploads are bounded, and the
scanner keeps a bounded queue. These controls do not make an untrusted network
safe. Keep firewall rules scoped to the intended private subnet.

Install model checkpoints only from trusted sources: PyTorch checkpoints and
third-party model libraries are executable software inputs, not inert pictures.
Keep downloaded models and personal scan data outside version control. Keep
Android signing keys private and back them up separately.

Report vulnerabilities privately to the repository maintainer, using GitHub
private vulnerability reporting if it is enabled. Do not include private scan
images, database exports, network credentials or signing material in public issues.
