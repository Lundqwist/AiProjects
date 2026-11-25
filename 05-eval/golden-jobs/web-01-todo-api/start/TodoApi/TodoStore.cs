namespace TodoApi;

public record Todo(int Id, string Title, bool Done);

public record NewTodo(string? Title);

/// <summary>Keeps todos in memory. Thread-safe; ids start at 1 and are never reused.</summary>
public class TodoStore
{
    private readonly object _lock = new();
    private readonly List<Todo> _items = new();
    private int _nextId = 1;

    public IReadOnlyList<Todo> All()
    {
        lock (_lock)
        {
            return _items.ToList();
        }
    }

    public Todo Add(string title)
    {
        lock (_lock)
        {
            var todo = new Todo(_nextId++, title, false);
            _items.Add(todo);
            return todo;
        }
    }
}
