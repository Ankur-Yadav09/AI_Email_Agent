
# asyncio allows us to run asynchronous functions.
import asyncio

# sys gives us the path to the Python interpreter currently in use.
import sys

# Path helps us work with file and folder paths.
from pathlib import Path

# Import the MCP client classes.
from mcp import ClientSession, StdioServerParameters

# Import the function that starts an MCP server and communicates through stdio.
from mcp.client.stdio import stdio_client

# Get the root directory of our project.
# Since this file is inside "agents", parent.parent points to the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# Tell the MCP client how to start our MCP server.
server_params = StdioServerParameters(
    # Use the same Python interpreter as this client.
    command=sys.executable,

    # Provide the path of the MCP server file to execute.
    args=[str(PROJECT_ROOT / "agents" / "mcp_server.py")],
)


# Define the main asynchronous function for our MCP client.
async def main():

    # Start the MCP server as a subprocess.
    # stdio_client provides two communication streams:
    # "read" receives messages from the server.
    # "write" sends messages to the server.
    async with stdio_client(server_params) as (read, write):  # noqa: SIM117

        # Create a client session using these communication streams.
        async with ClientSession(read, write) as session:

            # Initialize the MCP connection before making requests.
            await session.initialize()

            print("Connected to MCP Server successfully!")

            # Ask the server which tools it provides.
            tools_response = await session.list_tools()

            # Display the name and description of every available tool.
            print("\nAvailable MCP Tools:")

            for tool in tools_response.tools:
                print(f"- {tool.name}: {tool.description}")

            # We will now call the account balance tool.
            print("\nCalling account balance tool...")

            # Send a request to the MCP server to execute this tool.
            result = await session.call_tool(
                "get_account_balance",

                # Pass the arguments required by the tool.
                arguments={
                    "account_reference": "1234567890",
                    "customer_id": "CUST001",
                },
            )

            # Print the result returned by the MCP server.
            print("\nTool Result:")

            # Tool results can contain different content types.
            # Print text content when the server returns it.
            for item in result.content:
                if hasattr(item, "text"):
                    print(item.text) # type: ignore

            # Check whether the tool call reported an error.
            if result.is_error:
                print("The tool reported an error.")


# Run the main function when this file is executed directly.
if __name__ == "__main__":
    asyncio.run(main())
