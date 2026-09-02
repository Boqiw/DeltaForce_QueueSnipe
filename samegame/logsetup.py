import json, logging, sys

class JsonFormatter(logging.Formatter):
    def format(self, record):
        standard = {"name", "msg", "args", "levelname", "levelno", "pathname",
                    "filename", "module", "exc_info", "exc_text", "stack_info",
                    "lineno", "funcName", "created", "msecs", "relativeCreated",
                    "thread", "threadName", "processName", "process", "message"}
        fields = {k: v for k, v in record.__dict__.items() if k not in standard}
        return json.dumps({"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                           "level": record.levelname, "logger": record.name,
                           "message": record.getMessage(), **fields}, ensure_ascii=False, default=str)

def configure(level="INFO"):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), handlers=[handler], force=True)
