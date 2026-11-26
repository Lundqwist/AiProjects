import { Routes, Route } from 'react-router-dom';

function Home() {
  return (
    <main>
      <h1>{{Name}}</h1>
      <p>The site is being built.</p>
    </main>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
    </Routes>
  );
}
