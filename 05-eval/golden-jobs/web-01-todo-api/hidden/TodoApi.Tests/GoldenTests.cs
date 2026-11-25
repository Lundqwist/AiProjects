using System.Net;
using System.Net.Http.Json;
using Microsoft.AspNetCore.Mvc.Testing;
using Xunit;

namespace TodoApi.Tests;

// Hidden checks for the golden job: copied in only when the job is checked, never shown to the model.
public class GoldenTests : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly HttpClient _client;

    public GoldenTests(WebApplicationFactory<Program> factory) => _client = factory.CreateClient();

    private record Item(int Id, string Title, bool Done);

    private async Task<Item> Create(string title)
    {
        var post = await _client.PostAsJsonAsync("/api/todos", new { title });
        Assert.Equal(HttpStatusCode.Created, post.StatusCode);
        return (await post.Content.ReadFromJsonAsync<Item>())!;
    }

    [Fact]
    public async Task Get_by_id_returns_the_todo()
    {
        var made = await Create("Call the client");
        var got = await _client.GetFromJsonAsync<Item>($"/api/todos/{made.Id}");
        Assert.Equal(made, got);
    }

    [Fact]
    public async Task Get_missing_id_is_404()
    {
        var r = await _client.GetAsync("/api/todos/987654");
        Assert.Equal(HttpStatusCode.NotFound, r.StatusCode);
    }

    [Fact]
    public async Task Delete_removes_and_returns_204()
    {
        var made = await Create("Temporary");
        var del = await _client.DeleteAsync($"/api/todos/{made.Id}");
        Assert.Equal(HttpStatusCode.NoContent, del.StatusCode);

        var after = await _client.GetAsync($"/api/todos/{made.Id}");
        Assert.Equal(HttpStatusCode.NotFound, after.StatusCode);
        var all = await _client.GetFromJsonAsync<List<Item>>("/api/todos");
        Assert.DoesNotContain(all!, t => t.Id == made.Id);
    }

    [Fact]
    public async Task Delete_missing_id_is_404()
    {
        var r = await _client.DeleteAsync("/api/todos/987655");
        Assert.Equal(HttpStatusCode.NotFound, r.StatusCode);
    }

    [Theory]
    [InlineData("{}")]
    [InlineData("{\"title\":\"\"}")]
    [InlineData("{\"title\":\"   \"}")]
    [InlineData("{\"title\":null}")]
    public async Task Post_without_a_real_title_is_400(string body)
    {
        var r = await _client.PostAsync("/api/todos",
            new StringContent(body, System.Text.Encoding.UTF8, "application/json"));
        Assert.Equal(HttpStatusCode.BadRequest, r.StatusCode);
    }

    [Fact]
    public async Task Titles_are_stored_trimmed()
    {
        var made = await Create("  Buy milk  ");
        Assert.Equal("Buy milk", made.Title);
        var got = await _client.GetFromJsonAsync<Item>($"/api/todos/{made.Id}");
        Assert.Equal("Buy milk", got!.Title);
    }
}
