
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langgraph.types import Command

from agents.email_workflow import email_workflow


async def main():
    config = {
        "configurable": {
            "thread_id": "email-demo-001"
        }
    }

    # Start the workflow. It should pause at human_review.
    result = await email_workflow.ainvoke(
        {
            "email_text": (
                "Hello, please check my account balance."
            )
        }, # type: ignore
        config=config, # type: ignore
    )

    # Display the interrupt payload returned by LangGraph.
    interrupts = result.get("__interrupt__", [])

    if not interrupts:
        print("Workflow did not pause for human review.")
        print("Current result:", result)
        return

    review_request = interrupts[0].value

    print("\n========== HUMAN REVIEW ==========")
    print("\nOriginal email:")
    print(review_request["original_email"])

    print("\nGenerated draft:")
    print(review_request["draft"])

    print("\n1. Approve")
    print("2. Reject")

    choice = input("\nEnter your choice (1/2): ").strip()

    if choice == "1":
        feedback = input("Optional review feedback: ").strip()

        decision = {
            "approved": True,
            "feedback": feedback,
        }

    elif choice == "2":
        feedback = input("Reason for rejection: ").strip()

        decision = {
            "approved": False,
            "feedback": feedback or "Draft rejected by reviewer.",
        }

    else:
        print("Invalid choice. Workflow remains paused.")
        return

    # Resume the SAME workflow execution with the review decision.
    final_result = await email_workflow.ainvoke(
        Command(resume=decision),
        config=config, # type: ignore
    )

    print("\n========== REVIEW RESULT ==========")
    print("Approval status:", final_result.get("approval_status"))
    print("Workflow status:", final_result.get("status"))
    print("Draft:", final_result.get("draft"))
    print("Feedback:", final_result.get("reviewer_feedback"))


if __name__ == "__main__":
    asyncio.run(main())
