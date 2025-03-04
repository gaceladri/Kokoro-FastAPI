#!/bin/bash
set -e

# Configuration
STACK_NAME="stripe-usage-reporter"
ENVIRONMENT=${1:-prod}  # Default to prod if not specified
REGION=${AWS_REGION:-us-east-1}  # Use AWS_REGION env var or default to us-east-1
ENV_FILE="../config/.env.example"  # Path to environment file

# Read environment variables from .env file
echo "Reading configuration from $ENV_FILE..."
if [ -f "$ENV_FILE" ]; then
    # Export all variables from the .env file
    export $(grep -v '^#' "$ENV_FILE" | xargs)
    
    # Map variables to the ones used in this script
    STRIPE_API_KEY=${STRIPE_SECRET_KEY}
    SUPABASE_SERVICE_KEY=${SUPABASE_KEY}
    
    # Use stripe-reporter profile by default instead of stripe-reporter-admin
    AWS_PROFILE=${AWS_ADMIN_PROFILE:-stripe-reporter}
    
    echo "Loaded configuration successfully"
    echo "Using Supabase URL: ${SUPABASE_URL}"
    echo "Using AWS Profile: ${AWS_PROFILE}"
else
    echo "Warning: Environment file $ENV_FILE not found. Will prompt for values."
    AWS_PROFILE=${AWS_PROFILE:-stripe-reporter}  # Use AWS_PROFILE env var or default to stripe-reporter
fi

DEPLOYMENT_BUCKET="${STACK_NAME}-deployment-${ENVIRONMENT}"
BUILD_DIR="build"
PACKAGE_FILE="packaged.yaml"
DEPENDENCIES_DIR="lambda_dependencies"

# Check for AWS CLI and set the path
if command -v /usr/local/bin/aws &> /dev/null; then
    AWS_CMD="/usr/local/bin/aws"
    echo "Using AWS CLI at: $AWS_CMD"
elif command -v aws &> /dev/null; then
    AWS_CMD="aws"
    echo "Using AWS CLI from PATH: $(which aws)"
else
    echo "AWS CLI is not installed. Please install it first."
    exit 1
fi

# Check for Docker
if ! command -v docker &> /dev/null; then
    echo "Docker is not installed. Please install it first."
    exit 1
fi

echo "Deploying Stripe Usage Reporter Lambda to ${ENVIRONMENT} environment in ${REGION} using profile ${AWS_PROFILE}"

# Create build directory
mkdir -p $BUILD_DIR

# Create dependencies directory for layer packaging with proper permissions
# Don't try to remove previous directory if it exists, just create a new one with a timestamp
DEPENDENCIES_DIR="${DEPENDENCIES_DIR}_$(date +%s)"
mkdir -p $DEPENDENCIES_DIR

# Create virtual environment for SAM CLI
echo "Creating virtual environment..."
python -m venv venv
# Use . instead of source for better shell compatibility
. venv/bin/activate

# Install dependencies for deployment tools
echo "Installing deployment tools..."
pip install aws-sam-cli

# Use Docker to install and package Lambda dependencies all within the container
echo "Building Lambda dependencies in Docker Lambda container..."
# Copy requirements.txt to the deployment directory
cp requirements.txt $DEPENDENCIES_DIR/

# Create a Dockerfile for building and packaging dependencies
cat > $DEPENDENCIES_DIR/Dockerfile << EOF
FROM public.ecr.aws/lambda/python:3.9

WORKDIR /var/task
COPY requirements.txt .

# Install zip utility first, then install dependencies and package them
RUN yum install -y zip && \
    pip install -r requirements.txt -t /python && \
    mkdir -p /output && \
    cd /python && \
    zip -r /output/dependencies.zip .

# Provide a default command
CMD ["echo", "Dependencies built and packaged successfully"]
EOF

# Build the Docker image
docker build -t lambda-dependencies $DEPENDENCIES_DIR

# Run a container to create the zip file
echo "Packaging dependencies in Docker container..."
docker run --rm --entrypoint cp -v $(pwd):/mnt lambda-dependencies /output/dependencies.zip /mnt/dependencies-layer.zip

echo "Dependency layer created: dependencies-layer.zip"

# Create deployment bucket if it doesn't exist
echo "Creating S3 bucket for deployments if it doesn't exist..."
if ! $AWS_CMD s3api head-bucket --bucket $DEPLOYMENT_BUCKET --region $REGION --profile $AWS_PROFILE 2>/dev/null; then
    $AWS_CMD s3 mb s3://$DEPLOYMENT_BUCKET --region $REGION --profile $AWS_PROFILE
    $AWS_CMD s3api put-bucket-encryption \
        --bucket $DEPLOYMENT_BUCKET \
        --server-side-encryption-configuration '{"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]}' \
        --profile $AWS_PROFILE
fi

# Upload the dependencies layer to S3
echo "Uploading dependencies layer to S3..."
$AWS_CMD s3 cp dependencies-layer.zip s3://$DEPLOYMENT_BUCKET/dependencies-layer.zip --region $REGION --profile $AWS_PROFILE

# Prompt for sensitive parameters if not provided
if [ -z "$STRIPE_API_KEY" ]; then
    read -sp "Enter your Stripe API key: " STRIPE_API_KEY
    echo
fi

if [ -z "$SUPABASE_URL" ]; then
    read -p "Enter your Supabase URL: " SUPABASE_URL
fi

if [ -z "$SUPABASE_SERVICE_KEY" ]; then
    read -sp "Enter your Supabase service key: " SUPABASE_SERVICE_KEY
    echo
fi

# Show a summary of what we're deploying
echo "Deployment Summary:"
echo "  Stack Name: $STACK_NAME-$ENVIRONMENT"
echo "  Region: $REGION"
echo "  AWS Profile: $AWS_PROFILE"
echo "  Supabase URL: $SUPABASE_URL"
echo "  Stripe & Supabase Keys: [Configured]"

# Create a clean directory for Lambda code only (without dependencies)
echo "Preparing Lambda code package..."
LAMBDA_CODE_DIR="lambda_code"
# Remove existing directory if it exists and create a fresh one
rm -rf $LAMBDA_CODE_DIR
mkdir -p $LAMBDA_CODE_DIR
cp handler.py $LAMBDA_CODE_DIR/

# Create an updated template with the correct S3 URI for the layer
echo "Creating updated template file..."
sed "s|ContentUri: lambda_code/|ContentUri: s3://$DEPLOYMENT_BUCKET/dependencies-layer.zip|" template.yaml > template-updated.yaml

# Package the Lambda function
echo "Packaging the Lambda function..."
sam package \
    --template-file template-updated.yaml \
    --s3-bucket $DEPLOYMENT_BUCKET \
    --output-template-file $BUILD_DIR/$PACKAGE_FILE \
    --region $REGION \
    --profile $AWS_PROFILE

# Deploy the Lambda function
echo "Deploying the Lambda function..."
sam deploy \
    --template-file $BUILD_DIR/$PACKAGE_FILE \
    --stack-name $STACK_NAME-$ENVIRONMENT \
    --parameter-overrides \
        Environment=$ENVIRONMENT \
        StripeApiKeyParam=$STRIPE_API_KEY \
        SupabaseUrlParam=$SUPABASE_URL \
        SupabaseServiceKeyParam=$SUPABASE_SERVICE_KEY \
    --capabilities CAPABILITY_IAM \
    --region $REGION \
    --profile $AWS_PROFILE

# Clean up (with error handling)
echo "Cleaning up..."
deactivate

# Clean up files safely
echo "Cleaning temporary files..."
# Use find to delete files the current user has permission to delete
find $DEPENDENCIES_DIR -type f -writable -exec rm -f {} \; 2>/dev/null || true

# Remove directories if possible
rmdir $DEPENDENCIES_DIR 2>/dev/null || echo "Note: Could not remove $DEPENDENCIES_DIR completely. This is OK."

# Remove other temporary files
rm -f dependencies-layer.zip 2>/dev/null || echo "Note: Could not remove dependencies-layer.zip. This is OK."
rm -f template-updated.yaml 2>/dev/null || echo "Note: Could not remove template-updated.yaml. This is OK."

# Get the Lambda function details
FUNCTION_NAME=$($AWS_CMD cloudformation describe-stacks --stack-name $STACK_NAME-$ENVIRONMENT --region $REGION --profile $AWS_PROFILE --query "Stacks[0].Outputs[?OutputKey=='StripeReporterLambdaFunctionName'].OutputValue" --output text)
DASHBOARD_URL=$($AWS_CMD cloudformation describe-stacks --stack-name $STACK_NAME-$ENVIRONMENT --region $REGION --profile $AWS_PROFILE --query "Stacks[0].Outputs[?OutputKey=='StripeReporterDashboardUrl'].OutputValue" --output text)

echo "Deployment complete!"
echo "Lambda function name: $FUNCTION_NAME"
echo "CloudWatch Dashboard: $DASHBOARD_URL"
echo ""
echo "To test the function, run:"
echo "$AWS_CMD lambda invoke --function-name $FUNCTION_NAME --region $REGION --profile $AWS_PROFILE output.json"
echo ""
echo "To view the logs, run:"
echo "$AWS_CMD logs get-log-events --log-group-name /aws/lambda/$FUNCTION_NAME --region $REGION --profile $AWS_PROFILE --log-stream-name \$($AWS_CMD logs describe-log-streams --log-group-name /aws/lambda/$FUNCTION_NAME --region $REGION --profile $AWS_PROFILE --query 'logStreams[0].logStreamName' --output text)" 