using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using TodoApi;
using Xunit;

namespace TodoApi.Tests;

public class ListTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly HttpClient _client;

    public ListTests(WebApplicationFactory<Program> factory) => _client = factory.CreateClient();

    [Fact]
    public async Task Post_then_list_shows_the_new_todo()
    {
        var post = await _client.PostAsJsonAsync("/api/todos", new { title = "Write tests" });
        Assert.Equal(HttpStatusCode.Created, post.StatusCode);

        var all = await _client.GetFromJsonAsync<List<Todo>>("/api/todos");
        Assert.Contains(all!, t => t.Title == "Write tests" && !t.Done);
    }
}
