#!/bin/bash
# Script to set up environment variables securely

# Set default config directory
CONFIG_DIR="./config"
ENV_FILE="$CONFIG_DIR/.env"
ENV_EXAMPLE="$CONFIG_DIR/.env.example"

# Create config directory if it doesn't exist
mkdir -p "$CONFIG_DIR"

# Check if .env file already exists
if [ -f "$ENV_FILE" ]; then
    echo "Environment file $ENV_FILE already exists."
    read -p "Do you want to overwrite it? (y/n): " overwrite
    if [ "$overwrite" != "y" ]; then
        echo "Keeping existing environment file."
        exit 0
    fi
fi

# Copy example file if it exists, otherwise create a new one
if [ -f "$ENV_EXAMPLE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "Created $ENV_FILE from example template."
else
    # Create basic .env file
    cat > "$ENV_FILE" << EOL
# Supabase Configuration
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_KEY=your-supabase-key

# Stripe Configuration
STRIPE_SECRET_KEY=your-stripe-secret-key

# Feature Flags
ENABLE_USAGE_TRACKING=true
DOWNLOAD_MODEL=true

# API Settings
API_HOST=0.0.0.0
API_PORT=8880

# Device Settings
USE_GPU=true
DEVICE=gpu

# Demo Configuration
DEMO_MAX_CHARACTERS=700
DEMO_DAILY_LIMIT=100
EOL
    echo "Created new $ENV_FILE file."
fi

# Set secure permissions
chmod 600 "$ENV_FILE"
echo "Set secure permissions (600) on $ENV_FILE"

# Prompt user to edit the file
echo ""
echo "Please edit $ENV_FILE to set your secure environment variables."
echo "You can use your preferred text editor, for example:"
echo "  nano $ENV_FILE"
echo ""
echo "After editing, you can run the application with:"
echo "  CONFIG_DIR=$CONFIG_DIR docker-compose -f docker/gpu/docker-compose.yml up"
echo "  or"
echo "  CONFIG_DIR=$CONFIG_DIR docker-compose -f docker/cpu/docker-compose.yml up"
echo ""
echo "For production environments, consider using Docker secrets or a secure secrets manager." 