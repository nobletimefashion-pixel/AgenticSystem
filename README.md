# Nexus Agent

AI coding agent for the terminal.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/nobletimefashion-pixel/Agentic-AI-/main/install.sh | bash
```

This installs:
- `uv` (if missing)
- Creates `~/.nexus-agent/` with an isolated Python environment
- Adds `nexus` to `~/.local/bin/`

## Usage

```bash
# Interactive mode
nexus

# Single prompt
nexus "find all TODO comments in the project"

# RAG mode — query PDF documents
nexus --rag /path/to/pdfs
```

## Development

```bash
git clone https://github.com/nobletimefashion-pixel/Agentic-AI-.git
cd Agentic-AI-
uv venv && source .venv/bin/activate
uv pip install -e .
nexus
```

## to run the ui
python main.py --ui                     # opens browser automatically
python main.py --ui --ui-port 8080      # custom port
python main.py --ui --ui-host 0.0.0.0  # expose on LAN

## To Run the Agent locally download the github repo and run

python main.py --ui --ui-host 0.0.0.0

then visit the site 
https://nobletimefashion-pixel.github.io/AgenticSystem/
