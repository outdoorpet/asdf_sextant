var map = L.map('map').setView([0, 0], 0);
var layer = new L.StamenTileLayer("toner");
map.addLayer(layer);

var events = {};

function addEvent(event_id, row_index, latitude, longitude, a_color, p_color) {
    var marker = new L.CircleMarker(
        L.latLng(latitude, longitude), {
            radius: 10,
            color: "Black"
    }).on("click", circleClick);

    marker.status = "--";

    marker.myCustomEventID = event_id;
    marker.myCustomRowID = row_index;

    map.addLayer(marker);

    events[event_id] = {
        "marker": marker,
        "latitude": latitude,
        "longitude": longitude,
        "active_color": a_color,
        "passive_color": p_color};

//    setMarkerInactive(events[event_id]);

}


function setMarkerActive(value) {
    if (value.marker.status != "active") {
        value.marker.setStyle({color: value.active_color, opacity: 0.8, fillOpacity: 0.5});
        value.marker.bringToFront()
        value.marker.status = "active";
    }
}


function setMarkerInactive(value) {
    if (value.marker.status != "passive") {
        value.marker.setStyle({color: value.passive_color, opacity: 0.6, fillOpacity: 0.3});
        value.marker.status = "passive";
    }
}


function setAllInactive() {
    _.forEach(events, function(value, key) {
        setMarkerInactive(value);
    });
}


function setAllActive() {
    _.forEach(events, function(value, key) {
        setMarkerActive(value);
    });
}


function highlightEvent(event_id) {
    setAllInactive();
    var value = events[event_id];
    setMarkerActive(value)
}

function resetMarkerSize() {
    _.forEach(events, function(value, key) {
        value.marker.setRadius(10);
    });
}

function removeEventMarkers() {
    _.forEach(events, function(value, key) {
        value.marker.removeMarker();
    });
}


function circleClick(e) {
    var clickedCircle = e.target;
    clickedCircle.bindPopup(clickedCircle.myCustomEventID).openPopup()
}

