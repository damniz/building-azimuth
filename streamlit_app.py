import streamlit as st

from azimuth.footprint import FootprintNotFoundError
from azimuth.geocode import GeocodeError
from azimuth.imagery import ImageryError
from azimuth.pipeline import compute_building_azimuth

st.set_page_config(page_title="Building azimuth", page_icon=":material/explore:")

st.title("Building azimuth")
st.write("Enter an address to find the main orientation of the building's roof.")

with st.form("address_form"):
    address = st.text_input("Address", placeholder="e.g. Grote Markt 1, Antwerp, Belgium")
    submitted = st.form_submit_button("Find azimuth", type="primary")

if submitted:
    if not address.strip():
        st.error("Please enter an address.")
    else:
        try:
            with st.spinner("Looking up the building..."):
                result = compute_building_azimuth(address)
        except (GeocodeError, FootprintNotFoundError, ImageryError) as exc:
            st.error(str(exc))
        else:
            st.success(f"Found: {result.display_name}")

            primary_deg = result.primary.bearing_deg
            alt_deg = (primary_deg + 180.0) % 360.0
            st.metric("Roof azimuth", f"{primary_deg:.0f}° / {alt_deg:.0f}°")
            st.caption(
                "Shown as a line, not a direction: a roof ridge inferred from the "
                "footprint has no inherent forward/backward orientation."
            )

            st.image(result.image, width="stretch")
            if result.image_attribution:
                st.caption(result.image_attribution)
