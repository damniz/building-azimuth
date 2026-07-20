import streamlit as st

from azimuth.footprint import FootprintNotFoundError
from azimuth.geocode import GeocodeError
from azimuth.imagery import ImageryError
from azimuth.pipeline import compute_building_azimuth

st.set_page_config(page_title="Building azimuth", page_icon=":material/explore:")

st.title("Building azimuth")
st.write("Enter an address to find the main orientation of the building's roof.")

_ORIENTATION_OPTIONS = {
    "Ridge detection": None,
    "Ridge along the long side": "along",
    "Ridge along the short side": "across",
    "Flat roof": "flat",
}

with st.form("address_form"):
    address = st.text_input("Address", placeholder="e.g. Grote Markt 1, Antwerp, Belgium")
    orientation_choice = st.segmented_control(
        "Roof ridge",
        options=list(_ORIENTATION_OPTIONS),
        default="Ridge detection",
        help=(
            "The azimuth is estimated from the building's footprint shape, which isn't "
            "always right. If you know which way the ridge actually runs, set it here."
        ),
    )
    submitted = st.form_submit_button("Find azimuth", type="primary")

if submitted:
    if not address.strip():
        st.error("Please enter an address.")
    else:
        manual_orientation = _ORIENTATION_OPTIONS[orientation_choice or "Ridge detection"]
        try:
            with st.spinner("Looking up the building..."):
                result = compute_building_azimuth(address, manual_orientation)
        except (GeocodeError, FootprintNotFoundError, ImageryError) as exc:
            st.error(str(exc))
        else:
            st.success(f"Found: {result.display_name}")

            primary_deg = result.primary.bearing_deg
            st.metric("Roof azimuth", f"{primary_deg:.0f}°")
            if result.roof_orientation_hint == "across":
                st.caption("Adjusted: ridge set to run along the shorter side.")
            elif result.roof_orientation_hint == "along":
                st.caption("Confirmed: ridge set to run along the longer side.")
            elif result.roof_orientation_hint == "flat":
                st.caption("Flat roof: azimuth estimated from the building's footprint shape.")
            elif result.ridge_detected:
                st.caption("Detected from a visible roof ridge line in the image.")

            st.image(result.image, width="stretch")
            if result.image_attribution:
                st.caption(result.image_attribution)
