"""
AWS Lambda function to report character overages to Stripe.

This Lambda identifies subscriptions with character usage exceeding their included limits
and reports the overages to Stripe for metered billing. It should be scheduled to run daily
via CloudWatch Events/EventBridge.
"""

import logging
import os
import time
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import stripe
from supabase import create_client

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Load environment variables
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")


class UsageReporter:
    """Handles reporting of character overages to Stripe."""

    def __init__(self):
        """Initialize the usage reporter with Supabase and Stripe clients."""
        if not STRIPE_API_KEY:
            raise ValueError("STRIPE_API_KEY environment variable is required")
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables are required"
            )

        # Initialize Stripe
        stripe.api_key = STRIPE_API_KEY

        # Initialize Supabase client
        self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    async def report_character_overages(self) -> Tuple[int, int, List[str]]:
        """
        Identify character overages and report them to Stripe.

        Returns:
            Tuple[int, int, List[str]]: (Total overages found, Successfully reported count, Errors)
        """
        try:
            start_time = time.time()
            logger.info("Starting character overage reporting process")

            # Get overages using the database function
            overages = await self._identify_character_overages()

            if not overages:
                logger.info("No character overages to report")
                return 0, 0, []

            logger.info(f"Found {len(overages)} subscriptions with character overages")

            # Report each overage to Stripe
            success_count = 0
            errors = []

            for overage in overages:
                try:
                    # Extract data from the overage record
                    subscription_id = overage.get("subscription_id")
                    subscription_item_id = overage.get("subscription_item_id")
                    reporting_units = overage.get("reporting_units", 0)

                    if not subscription_item_id:
                        error_msg = f"Missing subscription_item_id for subscription {subscription_id}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                        continue

                    # Report usage to Stripe
                    stripe_record = await self._report_to_stripe(
                        subscription_item_id=subscription_item_id,
                        quantity=reporting_units,
                    )

                    if not stripe_record:
                        error_msg = f"Failed to create Stripe usage record for subscription {subscription_id}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                        continue

                    # Mark usage as reported in the database
                    success = await self._mark_usage_reported(
                        subscription_id=subscription_id,
                        stripe_usage_record_id=stripe_record.id,
                    )

                    if success:
                        success_count += 1
                        logger.info(
                            f"Successfully reported {reporting_units} units for subscription {subscription_id}"
                        )
                    else:
                        error_msg = f"Failed to mark usage as reported for subscription {subscription_id}"
                        logger.error(error_msg)
                        errors.append(error_msg)

                except Exception as e:
                    error_msg = f"Error processing subscription {overage.get('subscription_id')}: {str(e)}"
                    logger.error(error_msg)
                    logger.error(traceback.format_exc())
                    errors.append(error_msg)

            duration = time.time() - start_time
            logger.info(
                f"Completed character overage reporting: {success_count}/{len(overages)} "
                f"processed successfully in {duration:.2f} seconds"
            )

            return len(overages), success_count, errors

        except Exception as e:
            error_msg = f"Failed to process character overages: {str(e)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            return 0, 0, [error_msg]

    async def _identify_character_overages(self) -> List[Dict]:
        """
        Call the database function to identify character overages.

        Returns:
            List[Dict]: List of subscription overages with reporting data
        """
        try:
            # Call the RPC function in the database
            result = self.supabase.rpc("identify_character_overages").execute()

            if not result.data:
                return []

            return result.data

        except Exception as e:
            logger.error(f"Error identifying character overages: {str(e)}")
            logger.error(traceback.format_exc())
            return []

    async def _report_to_stripe(
        self, subscription_item_id: str, quantity: int
    ) -> Optional[stripe.UsageRecord]:
        """
        Report usage to Stripe.

        Args:
            subscription_item_id: Stripe subscription item ID
            quantity: Number of units to report

        Returns:
            Optional[stripe.UsageRecord]: Stripe usage record or None if failed
        """
        try:
            # Create a usage record using the Stripe API
            usage_record = stripe.SubscriptionItem.create_usage_record(
                subscription_item_id,
                quantity=quantity,
                timestamp=int(datetime.now().timestamp()),
                action="increment",
            )

            return usage_record

        except stripe.error.StripeError as e:
            logger.error(f"Stripe API error: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Error reporting to Stripe: {str(e)}")
            return None

    async def _mark_usage_reported(
        self, subscription_id: str, stripe_usage_record_id: str
    ) -> bool:
        """
        Mark usage as reported in the database.

        Args:
            subscription_id: Subscription ID
            stripe_usage_record_id: Stripe usage record ID

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Call the RPC function in the database
            result = self.supabase.rpc(
                "mark_usage_reported",
                {
                    "p_subscription_id": subscription_id,
                    "p_stripe_usage_record_id": stripe_usage_record_id,
                },
            ).execute()

            # The function returns a boolean indicating success
            return result.data

        except Exception as e:
            logger.error(f"Error marking usage as reported: {str(e)}")
            return False


def lambda_handler(event, context):
    """
    AWS Lambda handler function.

    Args:
        event: The event dict that contains the parameters sent when the function is invoked
        context: Runtime information provided by AWS Lambda

    Returns:
        dict: Response containing execution results
    """
    import asyncio

    try:
        # Create event loop for asyncio
        loop = asyncio.get_event_loop()

        # Create and initialize the reporter
        reporter = UsageReporter()

        # Execute the reporting process
        logger.info("Starting stripe usage reporting")
        total, successful, errors = loop.run_until_complete(
            reporter.report_character_overages()
        )

        # Prepare response
        result = {
            "statusCode": 200,
            "total_overages": total,
            "successfully_reported": successful,
            "error_count": len(errors),
        }

        # Include errors in response if any occurred
        if errors:
            result["errors"] = errors[:10]  # Limit to 10 errors to avoid huge responses
            if len(errors) > 10:
                result["errors"].append(f"... and {len(errors) - 10} more errors")

        logger.info(
            f"Completed with {successful} successful reports out of {total} overages"
        )
        return result

    except Exception as e:
        logger.error(f"Fatal error in lambda function: {str(e)}")
        logger.error(traceback.format_exc())

        return {"statusCode": 500, "error": str(e), "traceback": traceback.format_exc()}
