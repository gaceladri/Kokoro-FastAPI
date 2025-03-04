# Stripe Usage Reporter Lambda

This Lambda function replaces the cron job used in the Docker container to report character overages to Stripe for metered billing. The Lambda function is triggered by a scheduled event from CloudWatch Events/EventBridge, running daily to ensure timely reporting of usage data.

## Features

- Identifies subscriptions with character usage exceeding their included limits
- Reports the overages to Stripe for metered billing
- Updates the database to mark usage as reported
- Handles errors gracefully with detailed logging
- Designed to run as a serverless function, decoupled from the main service

## Prerequisites

- AWS CLI configured with appropriate permissions
- Python 3.9 or higher
- Supabase database with the necessary tables and functions
- Stripe API key with appropriate permissions

## Setting Up AWS Credentials

### Creating a Dedicated IAM User (Recommended)

For better security, create a dedicated IAM user for deploying this Lambda function:

1. Log in to the AWS Management Console
2. Navigate to IAM (Identity and Access Management)
3. Click on "Users" in the left sidebar
4. Click the "Create user" button
5. Enter a name for your user (e.g., `stripe-reporter-admin`)
6. Under "Select AWS credential type" check "Access key - Programmatic access"
7. Click "Next: Permissions"
8. Click "Attach existing policies directly"
9. Click "Create policy"
10. Select the JSON tab and paste the following policy:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "cloudformation:*",
                "lambda:*",
                "events:*",
                "logs:*",
                "s3:*",
                "ssm:PutParameter",
                "ssm:GetParameter",
                "ssm:DeleteParameter",
                "iam:PassRole",
                "iam:CreateRole",
                "iam:PutRolePolicy"
            ],
            "Resource": "*"
        }
    ]
}
```

11. Click "Next: Tags" (add optional tags if desired)
12. Click "Next: Review"
13. Name your policy (e.g., `StripeReporterDeploymentPolicy`) and click "Create policy"
14. Go back to the user creation process, refresh the policy list, and select your new policy
15. Click through the next steps to create the user
16. **IMPORTANT**: Save the Access Key ID and Secret Access Key shown on the final screen

### Configure AWS CLI with the New User

Set up a named profile for your new user:

```bash
aws configure --profile stripe-reporter
```

When prompted, enter:
- The Access Key ID from step 16
- The Secret Access Key from step 16
- Your preferred region (e.g., us-east-1)
- Your preferred output format (e.g., json)

## Environment Variables

The following environment variables need to be configured in the Lambda function:

- `STRIPE_API_KEY`: Your Stripe API key
- `SUPABASE_URL`: URL of your Supabase instance
- `SUPABASE_SERVICE_KEY`: Service key for your Supabase instance

## Deployment

### Direct Deployment with Environment Variables

The deployment script will prompt you for the required secrets during deployment:

```bash
cd lambda_stripe_reporter
./deploy.sh
```

By default, the script uses the AWS profile named `stripe-reporter`. You can specify a different profile:

```bash
AWS_PROFILE=your-profile-name ./deploy.sh
```

The script will:
1. Prompt you for your Stripe API key, Supabase URL, and Supabase service key
2. Create a virtual environment
3. Install dependencies
4. Package the Lambda function
5. Deploy it to AWS with the provided secrets
6. Configure the CloudWatch Events/EventBridge trigger

You can specify a different environment by passing it as an argument:

```bash
./deploy.sh dev    # Deploys to dev environment
./deploy.sh staging   # Deploys to staging environment
./deploy.sh prod   # Deploys to production environment (default)
```

You can also provide the secrets as environment variables when running the script:

```bash
STRIPE_API_KEY=your_key SUPABASE_URL=your_url SUPABASE_SERVICE_KEY=your_key ./deploy.sh
```

### Alternative: Using AWS SSM Parameter Store (Requires Additional Permissions)

If you have the necessary IAM permissions, you can store your secrets in AWS SSM Parameter Store for enhanced security:

```bash
# First, add these permissions to your IAM user or role:
# - ssm:PutParameter
# - ssm:GetParameter
# - ssm:DeleteParameter

# Then store your secrets
aws ssm put-parameter --name /stripe-reporter/stripe-api-key --type SecureString --value "YOUR_STRIPE_API_KEY" --description "Stripe API key for usage reporting" --profile stripe-reporter
aws ssm put-parameter --name /stripe-reporter/supabase-url --type SecureString --value "YOUR_SUPABASE_URL" --description "Supabase URL for database access" --profile stripe-reporter
aws ssm put-parameter --name /stripe-reporter/supabase-service-key --type SecureString --value "YOUR_SUPABASE_SERVICE_KEY" --description "Supabase service key for database access" --profile stripe-reporter
```

### Verify Deployment

After deployment, you can test the Lambda function:

```bash
# Get the function name from the CloudFormation stack outputs
FUNCTION_NAME=$(aws cloudformation describe-stacks --stack-name stripe-usage-reporter-prod --profile stripe-reporter --query "Stacks[0].Outputs[?OutputKey=='StripeReporterLambdaFunctionName'].OutputValue" --output text)

# Invoke the function
aws lambda invoke --function-name $FUNCTION_NAME --profile stripe-reporter output.json

# Check the results
cat output.json
```

You can also view the function logs in CloudWatch:

```bash
# Get the latest log stream
LOG_STREAM=$(aws logs describe-log-streams --log-group-name /aws/lambda/$FUNCTION_NAME --profile stripe-reporter --query 'logStreams[0].logStreamName' --output text)

# View the logs
aws logs get-log-events --log-group-name /aws/lambda/$FUNCTION_NAME --log-stream-name $LOG_STREAM --profile stripe-reporter
```

## CloudWatch Events Schedule

By default, the Lambda function is scheduled to run daily at 2:00 AM UTC. You can modify this schedule by editing the CloudFormation template or using the AWS Console.

## Monitoring

The Lambda function logs all activities to CloudWatch Logs. You can monitor these logs for any errors or issues.

The deployment also creates a CloudWatch Dashboard that displays metrics for:
- Function invocations
- Error count
- Duration

You can access this dashboard using the URL provided after deployment.

## Architecture

This Lambda function is part of a larger architecture where:

1. Your text-to-speech service tracks usage in the Supabase database
2. This Lambda function periodically checks for usage overages
3. Identified overages are reported to Stripe for billing
4. The database is updated to mark usage as reported

This architecture decouples the billing functionality from your main service, making it more maintainable and scalable. 