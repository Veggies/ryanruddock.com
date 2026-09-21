"""Trips when the guestbook function is being hammered.

Subscribed to the SNS topic the CloudWatch invocation alarm publishes to.
Sets reserved concurrency on the target function to 0, which throttles every
further invocation immediately -- the function stops executing rather than
merely slowing down.

Re-arm with `terraform apply` (terraform will put the configured cap back), or:
    aws lambda put-function-concurrency \
        --function-name <name> --reserved-concurrent-executions 2
"""

import json
import os

import boto3

TARGET = os.environ["TARGET_FUNCTION"]
lam = boto3.client("lambda")


def handler(event, context):
    # SNS delivers the alarm as a JSON string inside the record
    states = []
    for record in event.get("Records", []):
        try:
            message = json.loads(record["Sns"]["Message"])
        except (KeyError, ValueError):
            continue
        states.append(message.get("NewStateValue"))

    # A CloudWatch alarm recovering to OK must not trip anything, so those are
    # ignored. Anything we cannot parse -- a budget notification, say, which is
    # not JSON -- falls through and trips: for a cost guardrail, failing toward
    # "stop spending" is the safe direction.
    if states and "ALARM" not in states:
        print("ignoring non-ALARM transition: %s" % states)
        return {"tripped": False, "reason": "not an ALARM transition"}

    lam.put_function_concurrency(
        FunctionName=TARGET,
        ReservedConcurrentExecutions=0,
    )
    print("TRIPPED: reserved concurrency for %s set to 0" % TARGET)
    return {"tripped": True, "function": TARGET}
