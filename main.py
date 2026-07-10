try:
    from .agent.core import Agent
except ImportError:
    from agent.core import Agent


def main() -> None:
    agent = Agent()
    agent.run()


if __name__ == "__main__":
    main()
