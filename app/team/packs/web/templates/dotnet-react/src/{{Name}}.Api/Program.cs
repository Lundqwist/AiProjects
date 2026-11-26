var builder = WebApplication.CreateBuilder(args);

builder.Services.AddControllers();

var app = builder.Build();

app.UseHttpsRedirection();
app.MapControllers();

app.Run();

// Lets the tests start the app with WebApplicationFactory<Program>.
public partial class Program { }
