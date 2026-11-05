"""The options the admin page can change. Each can still start from its environment variable; a saved value wins."""

from engine import DEFAULT_MODEL
from settings import Setting

SETTINGS = [
    Setting("update_check_hours", "Check for updates every (hours)", "float", 6.0, "SPARK_UPDATE_CHECK_HOURS", 0, 720,
            help="0 = only when you press Check now"),
    Setting("max_inflight", "Calls allowed to wait for the model", "int", 0, "SPARK_MAX_INFLIGHT", 0, 10000,
            help="Per replica; more get 429. 0 = 4"),
    Setting("cache_size", "Answers kept per replica", "int", 256, "SPARK_CACHE_SIZE", 0, 100000,
            help="The last answers for the same request, sent again without generating. Only temperature 0 requests are kept. 0 = off"),
    Setting("max_project_models", "Project models loaded per replica", "int", 2, "SPARK_MAX_PROJECT_MODELS", 1, 64,
            help="Each takes about as much memory as the base model"),
    Setting("project_models_dir", "Project models folder", "text", "", "SPARK_PROJECT_MODELS_DIR",
            help="A folder inside the container. Empty = project models off"),
    Setting("model", "Base model", "text", DEFAULT_MODEL, "SPARK_MODEL", restart=True, blank=False,
            help="A Hugging Face repository. After a restart, download it from the Updates card"),
    Setting("dtype", "Number format", "text", "bfloat16", "SPARK_DTYPE", restart=True, blank=False,
            help="bfloat16 or float32. float32 is faster on CPUs without native bfloat16 maths but uses twice the memory"),
    Setting("thinking", "Thinking (Ollama backend)", "text", "off", "SPARK_THINKING", restart=True, blank=False,
            help="off or on. on lets the model think before it answers: slower, and it needs a model that supports thinking"),
    Setting("hide_reasoning", "Hide reasoning written into the answer", "bool", True, "SPARK_HIDE_REASONING", restart=True,
            help="Drops everything before the last </think>. A streamed reply is then sent when complete, in one piece (Ollama backend)"),
    Setting("warm", "Load the model at start", "bool", True, "SPARK_WARM", restart=True),
]
