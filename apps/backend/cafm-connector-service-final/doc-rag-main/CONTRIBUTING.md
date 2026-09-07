# Contributing to Document RAG Platform

Thank you for your interest in contributing! 🎉

## Getting Started

1. **Fork the repository**
2. **Clone your fork**
   ```bash
   git clone https://github.com/YOUR_USERNAME/doc-rag.git
   cd doc-rag
   ```

3. **Set up development environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # or `venv\Scripts\activate` on Windows
   pip install -r requirements.txt
   pip install -r streamlit_requirements.txt
   ```

4. **Start services**
   ```bash
   docker compose up -d
   ```

## Development Workflow

1. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
   - Write clean, documented code
   - Follow existing code style
   - Add tests if applicable

3. **Test your changes**
   ```bash
   # Run unit tests
   pytest app/tests/ -v
   
   # Test API endpoints
   bash scripts/test_api.sh
   ```

4. **Commit your changes**
   ```bash
   git add .
   git commit -m "Add: brief description of your changes"
   ```

5. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```

6. **Open a Pull Request**
   - Go to the original repository
   - Click "New Pull Request"
   - Select your fork and branch
   - Describe your changes

## Code Style

- Follow PEP 8 for Python code
- Use type hints where applicable
- Add docstrings to functions and classes
- Keep functions focused and small

## Testing

- Add unit tests for new features
- Ensure all existing tests pass
- Test with both SQLite and PostgreSQL modes

## Documentation

- Update relevant `.md` files
- Add docstrings to new functions
- Include usage examples

## Questions?

Open an issue for:
- Bug reports
- Feature requests
- Questions about the codebase

Thank you for contributing! 🙏
