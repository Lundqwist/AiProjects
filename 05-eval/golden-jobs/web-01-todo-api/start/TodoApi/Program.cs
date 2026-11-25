using TodoApi;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddSingleton<TodoStore>();

var app = builder.Build();

app.MapGet("/api/todos", (TodoStore store) => store.All());

app.MapPost("/api/todos", (NewTodo input, TodoStore store) =>
{
    var todo = store.Add(input.Title!);
    return Results.Created($"/api/todos/{todo.Id}", todo);
});

app.Run();

public partial class Program { }
