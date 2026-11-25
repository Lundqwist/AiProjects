The client's todo API (ASP.NET Core 8 minimal API, project `TodoApi`) needs three changes:

1. `GET /api/todos/{id}` returns the todo as JSON (200), or 404 if there is no todo with that id.
2. `DELETE /api/todos/{id}` removes the todo and returns 204, or 404 if there is no todo with that id.
3. `POST /api/todos` must return 400 when the title is missing, empty or only whitespace.
   Valid titles are stored trimmed (" Buy milk " is stored as "Buy milk").

Keep the existing endpoints and tests working. `dotnet test TodoApi.Tests` must pass.
