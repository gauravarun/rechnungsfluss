import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App shell", () => {
  it("shows the product name and the review queue section", () => {
    render(<App />);
    expect(screen.getByRole("heading", { level: 1, name: "Rechnungsfluss" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Prüfwarteschlange" })).toBeInTheDocument();
  });

  it("states honestly that the review API is not connected yet", () => {
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent(/nicht verbunden/i);
  });
});
