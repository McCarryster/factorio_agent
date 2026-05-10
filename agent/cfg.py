
import enum
import dotenv
import os
from pathlib import Path


# Load environment variables from .env file
dotenv.load_dotenv()

# API credentials
API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")


# Langfuse set up
LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "")



class Models(enum.Enum):
    """Available Anthropic Claude model versions."""
    CLAUDE_SONNET_4_5 = "claude-sonnet-4-5-20251001"
    CLAUDE_HAIKU_4_5 = "claude-haiku-4-5-20251001"
    CLAUDE_OPUS_4_5 = "claude-opus-4-5-20251001"


# Default model configuration
DEFAULT_MODEL: str = Models.CLAUDE_HAIKU_4_5.value
MAX_TOKENS: int = 2048

# Path to look for or save skills
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"