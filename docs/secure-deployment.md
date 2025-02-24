# Secure Deployment Guidelines for Kokoro TTS

This document outlines best practices for securely deploying the Kokoro TTS application in production environments.

## Environment Variables

### Best Practices

1. **Never commit environment variables to version control**
   - Use `.env.example` files to document required variables without values
   - Add `.env` files to `.gitignore`

2. **Use a secure method to manage secrets**
   - For local development: Use the provided `setup-env.sh` script
   - For production: Consider using a secrets management service like:
     - Docker Secrets
     - Kubernetes Secrets
     - HashiCorp Vault
     - AWS Secrets Manager
     - Azure Key Vault

3. **Rotate credentials regularly**
   - Implement a process for regular rotation of API keys and secrets
   - Update deployment configurations when credentials change

## Docker Security

1. **Use non-root users in containers**
   - Our Dockerfiles should use `USER` directive to run as non-root
   - Verify with: `docker inspect --format '{{.Config.User}}' your-container`

2. **Avoid hardcoding secrets in Dockerfiles**
   - Never use `ENV` for sensitive values in Dockerfiles
   - Mount secrets at runtime or use environment files

3. **Scan container images for vulnerabilities**
   - Use tools like Trivy, Clair, or Snyk to scan images
   - Example: `trivy image kokoro-tts:latest`

## File Permissions

1. **Restrict access to configuration files**
   - Configuration files should have 600 permissions (owner read/write only)
   - Example: `chmod 600 config/.env`

2. **Secure model files**
   - Model files should be read-only: `chmod 400 models/*`
   - Ensure model directories are not world-writable

## Network Security

1. **Use HTTPS for all connections**
   - Configure TLS/SSL for all public endpoints
   - Use strong cipher suites and protocols

2. **Implement proper authentication**
   - Use OAuth2 or similar for API authentication
   - Implement rate limiting to prevent abuse

3. **Restrict network access**
   - Use firewalls to limit access to necessary ports only
   - Consider using a VPN for administrative access

## Regular Security Checks

1. **Run the security check script regularly**
   - `./security-check.sh` should be run before deployments
   - Consider integrating into CI/CD pipelines

2. **Keep dependencies updated**
   - Regularly update Python packages and system dependencies
   - Monitor security advisories for components used

3. **Perform periodic security audits**
   - Review access controls and permissions
   - Test for common vulnerabilities (OWASP Top 10)

## Incident Response

1. **Have a plan for security incidents**
   - Document steps to take if a breach is suspected
   - Include contact information for responsible parties

2. **Maintain backups**
   - Regularly backup configuration (not credentials)
   - Ensure backups are stored securely

## Compliance Considerations

1. **Data protection regulations**
   - Consider GDPR, CCPA, and other relevant regulations
   - Implement data minimization practices

2. **Logging and monitoring**
   - Implement secure logging practices
   - Monitor for unusual activity

---

For questions or to report security issues, please contact the security team. 