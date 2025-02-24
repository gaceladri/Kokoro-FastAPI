#!/bin/bash
# Script to check for potential security issues in the codebase

echo "🔒 Kokoro TTS Security Check 🔒"
echo "Scanning for potential security issues..."
echo ""

# Check for sensitive environment variables in Dockerfiles
echo "Checking Dockerfiles for sensitive environment variables..."
DOCKERFILE_ISSUES=$(grep -r "ENV.*\(SUPABASE_KEY\|STRIPE_SECRET_KEY\|API_KEY\)" --include="Dockerfile*" .)

if [ -n "$DOCKERFILE_ISSUES" ]; then
    echo "⚠️  WARNING: Found sensitive environment variables in Dockerfiles:"
    echo "$DOCKERFILE_ISSUES"
    echo "Consider removing these and using mounted config files instead."
    echo ""
else
    echo "✅ No sensitive environment variables found in Dockerfiles."
    echo ""
fi

# Check for .env files that might be accidentally committed
echo "Checking for .env files that might be accidentally committed..."
ENV_FILES=$(find . -name ".env" -not -path "./config/.env.example" -not -path "./.env.example")

if [ -n "$ENV_FILES" ]; then
    echo "⚠️  WARNING: Found .env files that might contain sensitive data:"
    echo "$ENV_FILES"
    echo "Consider adding these to .gitignore or removing them."
    echo ""
else
    echo "✅ No .env files found outside of expected locations."
    echo ""
fi

# Check for hardcoded credentials
echo "Checking for potential hardcoded credentials..."
CREDENTIAL_PATTERNS="(password|secret|key|token|credential).*['\"][a-zA-Z0-9_\.\-]{16,}['\"]"
HARDCODED_CREDENTIALS=$(grep -r -i -E "$CREDENTIAL_PATTERNS" --include="*.py" --include="*.js" --include="*.sh" . | grep -v "your_" | grep -v "example" | grep -v "placeholder" | grep -v "monkeypatch.setattr" | grep -v "test" | grep -v "mock")

if [ -n "$HARDCODED_CREDENTIALS" ]; then
    echo "⚠️  WARNING: Found potential hardcoded credentials:"
    echo "$HARDCODED_CREDENTIALS"
    echo "Consider using environment variables or secure storage instead."
    echo ""
else
    echo "✅ No obvious hardcoded credentials found."
    echo ""
fi

# Check for insecure permissions on config files
echo "Checking permissions on config files..."
if [ -d "./config" ]; then
    CONFIG_PERMS=$(find ./config -name "*.env*" -perm /o=rwx)
    
    if [ -n "$CONFIG_PERMS" ]; then
        echo "⚠️  WARNING: Found config files with insecure permissions:"
        echo "$CONFIG_PERMS"
        echo "Consider restricting permissions with: chmod 600 [file]"
        echo ""
    else
        echo "✅ Config file permissions look good."
        echo ""
    fi
else
    echo "ℹ️  Config directory not found, skipping permission check."
    echo ""
fi

# Check for sensitive data in docker-compose files
echo "Checking docker-compose files for sensitive data..."
COMPOSE_ISSUES=$(grep -r "SUPABASE_KEY\|STRIPE_SECRET_KEY\|API_KEY" --include="docker-compose*.yml" .)

if [ -n "$COMPOSE_ISSUES" ]; then
    echo "⚠️  WARNING: Found sensitive data in docker-compose files:"
    echo "$COMPOSE_ISSUES"
    echo "Consider using environment files or Docker secrets instead."
    echo ""
else
    echo "✅ No sensitive data found in docker-compose files."
    echo ""
fi

echo "Security check complete!"
echo "For more information on secure deployment, see: docs/secure-deployment.md" 