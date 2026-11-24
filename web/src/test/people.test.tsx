// The people layer (2026-09-06): names never render as payroll UPNs when the app
// knows better, avatars are stable per person, the picker chooses from the
// directory and falls back to a typed name + email, and the law name opens the
// pack's own duties.
import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Avatar, PeoplePicker, Person, avatarHue, initialsOf, personName, type PeopleIndex, type PickedPerson } from "../people";
import HiringRules from "../components/HiringRules";
import { mockApi, renderPage } from "./utils";
import type { Person as PersonRow } from "../types";

const KNOWN: PersonRow = {
  email: "u120729@example.com", name: "Shashank Srivastava", job_title: "IT Lead",
  photo: null, initials: "SS", hue: "violet", source: "sso",
};
const INDEX: PeopleIndex = new Map([[KNOWN.email, KNOWN]]);

describe("personName", () => {
  it("prefers a name already on the record, then the directory, then the address", () => {
    expect(personName("u120729@example.com", INDEX)).toBe("Shashank Srivastava");
    expect(personName("u120729@example.com", INDEX, "Typed Name")).toBe("Typed Name");
    // name-shaped addresses humanise; payroll ids do NOT become "U120729"
    expect(personName("arjun.test@example.com")).toBe("Arjun Test");
    expect(personName("u999999@example.com")).toBe("u999999@example.com");
    expect(personName("")).toBe("");
  });

  it("gives every address a stable colour and two-letter initials", () => {
    expect(avatarHue("a@b.com")).toBe(avatarHue("A@B.com"));
    expect(["green", "violet", "blue", "teal", "amber", "orange"]).toContain(avatarHue("anyone@example.com"));
    expect(initialsOf("Shashank Srivastava")).toBe("SS");
    expect(initialsOf("", "u120729@example.com")).toBe("U");
  });
});

describe("<Person> and <Avatar>", () => {
  it("renders the directory name with the address and title on hover", async () => {
    mockApi({});
    renderPage(<Person email="u120729@example.com" people={INDEX} />);
    const name = await screen.findByText("Shashank Srivastava");
    expect(name.closest(".person")).toHaveAttribute("title", "u120729@example.com · IT Lead");
    // the avatar uses the directory's hue (server and client agree on a known person)
    expect(name.closest(".person")!.querySelector(".avatar")!.className).toContain("avatar-hue-violet");
  });

  it("says You for the viewer when asked, and renders a photo when there is one", async () => {
    mockApi({});
    renderPage(
      <>
        <Person email="me@example.com" me="ME@example.com" you />
        <Avatar email="p@example.com" name="Photo Person" photo="data:image/png;base64,AAAA" />
      </>
    );
    expect(await screen.findByText("You")).toBeInTheDocument();
    expect(document.querySelector("img.avatar")).toHaveAttribute("src", "data:image/png;base64,AAAA");
  });
});

function PickerHarness() {
  const [v, setV] = useState<PickedPerson | null>(null);
  return (
    <>
      <PeoplePicker value={v} onChange={setV} ariaLabel="Hiring manager" manualLink />
      <output data-testid="picked">{v ? `${v.name} <${v.email}>` : "none"}</output>
    </>
  );
}

describe("<PeoplePicker>", () => {
  it("searches the directory as you type and shows the pick with a read-only address", async () => {
    const api = mockApi({
      "/api/people": { people: [{ ...KNOWN, name: "Bhavika Lillis", email: "bhavika.lillis@example.com", job_title: "Finance Director", hue: "teal" }], source: "local", note: null },
    });
    renderPage(<PickerHarness />);
    await userEvent.type(screen.getByLabelText("Hiring manager"), "pri");
    const option = await screen.findByRole("option", { name: /Bhavika Lillis/ });
    expect(option).toHaveTextContent("Finance Director · bhavika.lillis@example.com");
    expect(api.callsTo("/api/people")[0].url).toContain("q=pri");
    await userEvent.click(option);
    // the chip: name + address, and the address is text, not an input
    const chip = screen.getByTestId("picked-person");
    expect(chip).toHaveTextContent("Bhavika Lillis");
    expect(chip).toHaveTextContent("bhavika.lillis@example.com");
    expect(chip.querySelector("input")).toBeNull();
    expect(screen.getByTestId("picked")).toHaveTextContent("Bhavika Lillis <bhavika.lillis@example.com>");
    // Change clears the pick and brings the search back
    await userEvent.click(screen.getByRole("button", { name: "Change person" }));
    expect(screen.getByLabelText("Hiring manager")).toBeInTheDocument();
  });

  it("offers a manual name + email path for someone the directory does not know", async () => {
    mockApi({ "/api/people": { people: [], source: "local", note: null } });
    renderPage(<PickerHarness />);
    await userEvent.click(screen.getByRole("button", { name: /Enter a name and email manually/ }));
    const use = screen.getByRole("button", { name: /Use this person/ });
    expect(use).toBeDisabled(); // both are required
    await userEvent.type(screen.getByLabelText("Name"), "Gerard Branaola");
    await userEvent.type(screen.getByLabelText("Email"), "gerard.branaola@example.com");
    expect(use).toBeEnabled();
    await userEvent.click(use);
    expect(screen.getByTestId("picked")).toHaveTextContent("Gerard Branaola <gerard.branaola@example.com>");
  });

  it("explains itself when Graph is not available instead of failing silently", async () => {
    mockApi({ "/api/people": { people: [], source: "local", note: "Graph refused the request (the app's identity lacks User.Read.All consent) — ask the Cloud Admin to grant it." } });
    renderPage(<PickerHarness />);
    await userEvent.type(screen.getByLabelText("Hiring manager"), "zz");
    expect(await screen.findByText(/User.Read.All consent/)).toBeInTheDocument();
  });
});

describe("<HiringRules>", () => {
  it("opens the pack's own duties from the law name", async () => {
    mockApi({
      "/api/locations": {
        packs: [{
          code: "IN", label: "India", law: "DPDP Act 2023", keep_at_least_days: 1095, purge_by_days: 400,
          obligations: ["DPDP Act 2023: tell candidates an assistive screening tool processes their résumé."],
          notes: "Never caste, religion or community.", monitoring_categories: [],
        }],
        countries: [], us_states: [], nyc_pack: "US-NYC", nyc_state: "NY",
        processing_locations: null,
      },
    });
    renderPage(<HiringRules code="IN" law="DPDP Act 2023" />);
    const opener = await screen.findByRole("button", { name: "DPDP Act 2023" });
    await userEvent.hover(opener);
    await waitFor(() => expect(screen.getByText(/tell candidates an assistive screening tool/)).toBeInTheDocument());
    expect(screen.getByText(/audit record kept at least 3 years/)).toBeInTheDocument();
    expect(screen.getByText(/Never caste, religion or community/)).toBeInTheDocument();
  });
});
