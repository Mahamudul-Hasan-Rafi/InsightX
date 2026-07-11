# Debugging Guide for InsightX

This guide explains how to debug the InsightX application (FastAPI backend + Next.js frontend) in VS Code.

## Prerequisites

### Backend (Python)
1. Install the Python extension for VS Code: `ms-python.python`
2. Activate your virtual environment:
   ```powershell
   cd api
   .venv\Scripts\activate
   ```
3. Ensure `.env` file exists in `api/` folder with required configuration

### Frontend (JavaScript/TypeScript)
1. Install the JavaScript Debugger (built-in with VS Code)
2. Install dependencies:
   ```bash
   cd web
   npm install
   ```
3. Ensure `web/.env` file exists with `NEXT_PUBLIC_BASE_URL=http://localhost:8091`

## Debugging Configurations

The workspace includes five debugging configurations in `.vscode/launch.json`:

### 1. **FastAPI Backend** (Recommended for API debugging)
- **What it does**: Starts the FastAPI server with the Python debugger attached
- **Port**: 8000
- **How to use**:
  1. Open any Python file in `api/` folder
  2. Set breakpoints by clicking to the left of line numbers
  3. Press `F5` or go to Run & Debug → Select "FastAPI Backend"
  4. Server starts with hot-reload enabled
  5. Make API requests (via frontend or Postman) to hit your breakpoints

**Tips:**
- Breakpoints work in routes, services, and database models
- Use "Step Into" (F11) to debug library code
- Check Variables panel to inspect request objects
- Watch the Debug Console for print statements and logs

### 2. **Next.js Frontend (Chrome)**
- **What it does**: Starts Next.js dev server and launches Chrome with debugger
- **Port**: 8091
- **How to use**:
  1. Open any TypeScript/React file in `web/` folder
  2. Set breakpoints in your React components or API utility functions
  3. Press `F5` or select "Next.js Frontend (Chrome)"
  4. Chrome opens automatically with DevTools connected
  5. Interact with the UI to hit breakpoints

**Tips:**
- Breakpoints work in React components, hooks, and utility files
- Use Chrome DevTools for DOM inspection alongside VS Code debugging
- Check browser console for runtime errors

### 3. **Next.js Frontend (Edge)**
- Same as Chrome, but uses Microsoft Edge browser
- Useful if you prefer Edge or need to test Edge-specific behavior

### 4. **Attach to Next.js (Node)**
- **Advanced**: Attach to an already-running Next.js server
- **How to use**:
  1. Start Next.js manually with Node debugging enabled:
     ```bash
     cd web
     NODE_OPTIONS='--inspect' npm run dev
     ```
  2. Select "Attach to Next.js (Node)" and press F5
  3. Debugger attaches to the running process

### 5. **Full Stack (Backend + Frontend)** (Recommended for full-stack debugging)
- **What it does**: Starts both FastAPI and Next.js debuggers simultaneously
- **How to use**:
  1. Select "Full Stack (Backend + Frontend)" from the debug dropdown
  2. Press `F5`
  3. Both servers start with debuggers attached
  4. Set breakpoints in both backend and frontend code
  5. Debug the entire request flow from UI → API → Database

**Tips:**
- Best for debugging end-to-end features
- Watch the call stack across both processes
- Use "Stop All" button to stop both servers at once

## Common Debugging Scenarios

### Debugging an API Endpoint
1. Start "FastAPI Backend" debugger
2. Open `api/app/modules/<module>/router.py` and set breakpoint in the endpoint
3. Open browser or Postman and make a request to http://localhost:8000/api/v1/...
4. VS Code pauses at your breakpoint
5. Inspect variables, step through service calls, check database queries

### Debugging a React Component
1. Start "Next.js Frontend (Chrome)" debugger
2. Open `web/app/<component>.tsx` and set breakpoint in event handler or useEffect
3. Interact with the UI in the browser
4. VS Code pauses when code executes
5. Inspect React state, props, and Redux store

### Debugging Full Request Flow
1. Start "Full Stack (Backend + Frontend)" debugger
2. Set breakpoint in frontend API call (e.g., `web/lib/utils/fetch.utils.ts`)
3. Set breakpoint in backend endpoint (e.g., `api/app/modules/datasources/router.py`)
4. Trigger action in browser
5. Frontend breakpoint hits first → step through → backend breakpoint hits
6. See the complete data flow

### Debugging Background Tasks
1. Start "FastAPI Backend" debugger
2. Set breakpoint in `api/app/modules/annotations/service.py` background task
3. Trigger action that queues background task (e.g., save annotations)
4. Main request completes, then background task breakpoint hits
5. Debug async execution after response is sent

## Debugging Tips

### Backend
- **Environment variables**: Loaded from `api/.env` automatically
- **Database queries**: Set breakpoint before `session.execute()` to see SQL
- **Request inspection**: Hover over `request` object to see headers, body
- **Hot reload**: Code changes auto-reload the server while debugging
- **Logs**: Use `print()` or `logger.debug()` — output appears in Debug Console

### Frontend
- **Source maps**: Enabled by default — debug TypeScript directly, not compiled JS
- **React DevTools**: Install browser extension for additional React inspection
- **Network tab**: Use Chrome DevTools Network tab alongside VS Code debugger
- **Hot reload**: Code changes auto-refresh the page while debugging
- **Redux state**: Inspect with Redux DevTools extension or via Variables panel

### Performance
- **Slow debugging?** Disable "justMyCode" in launch.json to skip library code
- **Port conflicts?** Check if ports 8000 or 8091 are already in use
- **Debugger not attaching?** Restart VS Code and ensure extensions are installed

## Keyboard Shortcuts

| Action | Shortcut | Description |
|--------|----------|-------------|
| Start/Continue | F5 | Start debugging or continue execution |
| Step Over | F10 | Execute current line, skip function calls |
| Step Into | F11 | Enter function being called |
| Step Out | Shift+F11 | Exit current function |
| Stop | Shift+F5 | Stop debugging session |
| Restart | Ctrl+Shift+F5 | Restart debugging session |
| Toggle Breakpoint | F9 | Add/remove breakpoint at current line |

## Troubleshooting

### Python debugger won't start
- Ensure Python extension is installed: `ms-python.python`
- Verify virtual environment is activated
- Check that `api/.env` exists and is valid
- Try: `cd api && python -m uvicorn app.main:app --reload`

### Frontend debugger won't connect
- Ensure port 8091 is not in use
- Check that `web/.env` has `NEXT_PUBLIC_BASE_URL=http://localhost:8091`
- Try manual start: `cd web && npm run dev`
- Close other Chrome debugging sessions

### Breakpoints not hitting
- **Gray dot**: Source maps not loaded — wait for server to fully start
- **Crossed out**: Code not executed yet — trigger the action
- **Python**: Ensure file is in PYTHONPATH (should be automatic)
- **TypeScript**: Ensure sourceMapPathOverrides in launch.json match your setup

### Port already in use
```bash
# Windows: Find and kill process on port 8000
netstat -ano | findstr :8000
taskkill /PID <PID> /F

# Linux/Mac: Find and kill process on port 8000
lsof -ti:8000 | xargs kill -9
```

## Advanced: Debugging Tests

### Backend Tests
```powershell
cd api
pytest tests/ -v --pdb  # Drop into debugger on failure
```

Or use VS Code Test Explorer with breakpoints.

### Frontend Tests
(Tests not configured yet — add when test suite is implemented)

## Further Reading

- [VS Code Python Debugging](https://code.visualstudio.com/docs/python/debugging)
- [VS Code JavaScript Debugging](https://code.visualstudio.com/docs/nodejs/nodejs-debugging)
- [FastAPI Debugging Guide](https://fastapi.tiangolo.com/tutorial/debugging/)
- [Next.js Debugging](https://nextjs.org/docs/advanced-features/debugging)
