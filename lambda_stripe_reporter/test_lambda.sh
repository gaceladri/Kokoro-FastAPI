#!/bin/bash
set -e

# Configuration
STACK_NAME="stripe-usage-reporter"
ENVIRONMENT=${1:-prod}  # Default to prod if not specified
REGION=${AWS_REGION:-us-east-1}  # Use AWS_REGION env var or default to us-east-1
AWS_PROFILE=${AWS_PROFILE:-stripe-reporter}  # Use AWS_PROFILE env var or default to stripe-reporter

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

# Get the Lambda function name from CloudFormation
echo "Getting Lambda function name from CloudFormation..."
FUNCTION_NAME=$($AWS_CMD cloudformation describe-stacks --stack-name $STACK_NAME-$ENVIRONMENT --region $REGION --profile $AWS_PROFILE --query "Stacks[0].Outputs[?OutputKey=='StripeReporterLambdaFunctionName'].OutputValue" --output text)

if [ -z "$FUNCTION_NAME" ]; then
    echo "Error: Could not retrieve Lambda function name. Make sure the stack exists."
    exit 1
fi

echo "Found Lambda function: $FUNCTION_NAME"

# Invoke the Lambda function
echo "Invoking Lambda function..."
OUTPUT_FILE="lambda_output.json"
$AWS_CMD lambda invoke \
    --function-name $FUNCTION_NAME \
    --region $REGION \
    --profile $AWS_PROFILE \
    --payload '{}' \
    $OUTPUT_FILE

echo "Lambda function invoked. Output saved to $OUTPUT_FILE"
echo "Output contents:"
cat $OUTPUT_FILE

# Check for log streams
echo "Checking for log streams..."
LOG_GROUP_NAME="/aws/lambda/$FUNCTION_NAME"
$AWS_CMD logs describe-log-streams \
    --log-group-name $LOG_GROUP_NAME \
    --region $REGION \
    --profile $AWS_PROFILE \
    --max-items 1 \
    --order-by LastEventTime \
    --descending

# Get the most recent log stream
echo "Getting most recent log stream..."
LOG_STREAM=$($AWS_CMD logs describe-log-streams \
    --log-group-name $LOG_GROUP_NAME \
    --region $REGION \
    --profile $AWS_PROFILE \
    --max-items 1 \
    --order-by LastEventTime \
    --descending \
    --query 'logStreams[0].logStreamName' \
    --output text)

if [ "$LOG_STREAM" == "None" ]; then
    echo "No log streams found yet. Wait a moment and try again."
    exit 0
fi

echo "Found log stream: $LOG_STREAM"

# Get log events
echo "Getting log events..."
$AWS_CMD logs get-log-events \
    --log-group-name $LOG_GROUP_NAME \
    --log-stream-name "$LOG_STREAM" \
    --region $REGION \
    --profile $AWS_PROFILE 