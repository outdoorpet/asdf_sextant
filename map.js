var map = L.map('map').setView([0, 0], 0);

var layer = new L.StamenTileLayer("toner");
map.addLayer(layer);

var activeStnIcon = L.divIcon({
    className: 'svg-marker',
    html: '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" style="margin: 0 auto; width: 20px; height:20px;"><polygon style="fill:Red; stroke:#666666; stroke-width:2; stroke-opacity:0.5"points="0,0 20,0 10,20"/></svg>',
    iconSize: L.point(20, 20),
    iconAnchor: L.point(10, 20),
    popupAnchor: L.point(0,-20)
});

//var passiveStnIcon = L.divIcon({
//    className: 'svg-marker',
//    html: '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" style="margin: 0 auto; width: 20px; height:20px;"><polygon style="fill:#3D8EC9; stroke:#666666; stroke-width:2; stroke-opacity:0.5"points="0,0 20,0 10,20"/></svg>',
//    iconSize: L.point(20, 20),
//    iconAnchor: L.point(10, 20),
//    popupAnchor: L.point(0,-20)
//});
//
//var passiveRefStnIcon = L.divIcon({
//    className: 'svg-marker',
//    html: '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" style="margin: 0 auto; width: 20px; height:20px;"><polygon style="fill:Yellow; stroke:#666666; stroke-width:2; stroke-opacity:0.5"points="0,0 20,0 10,20"/></svg>',
//    iconSize: L.point(20, 20),
//    iconAnchor: L.point(10, 20),
//    popupAnchor: L.point(0,-20)
//});

// ============================================ For Stations ===============================================

var stations = {};

function addStation(station_id, latitude, longitude, in_html) {
    var marker = L.marker([latitude, longitude], {
        icon: activeStnIcon
    }).bindPopup(station_id).on("click", stationClick);

    marker.status = "initial";

    marker.myCustomStationID = station_id;

    map.addLayer(marker);

    var passive_stn_icon = L.divIcon({
        className: 'svg-marker',
        html: in_html,
        iconSize: L.point(20, 20),
        iconAnchor: L.point(10, 20),
        popupAnchor: L.point(0,-20)
    });

    stations[station_id] = {
        "marker": marker,
        "latitude": latitude,
        "longitude": longitude,
        "passive_icon": passive_stn_icon};

    setStnMarkerInactive(stations[station_id]);
}


// For Stations
function setStnMarkerActive(value) {
    if (value.marker.status != "active") {
        var pos = map.latLngToLayerPoint(value.marker.getLatLng()).round();
        value.marker.setIcon(activeStnIcon);
        value.marker.setZIndexOffset(101 - pos.y);
        value.marker.status = "active";
    }
}

function setStnMarkerInactive(value) {
    if (value.marker.status != "passive") {
        var pos = map.latLngToLayerPoint(value.marker.getLatLng()).round();
        value.marker.setIcon(value.passive_icon);
        value.marker.setZIndexOffset(100 - pos.y);
        value.marker.status = "passive";
    }
}

function setStnAllInactive() {
    _.forEach(stations, function(value, key) {
        setStnMarkerInactive(value);
    });
}


function setStnAllActive() {
    _.forEach(stations, function(value, key) {
        setStnMarkerActive(value);
    });
}


function highlightStation(station_id) {
    setStnAllInactive();
//    setStnMarkerInactive(stations[station_id]);
    var value = stations[station_id];
    setStnMarkerActive(value)
}


function stationClick(e) {
    var clickedStn = e.target;
    map.closePopup();

    highlightStation(clickedStn.myCustomStationID);
    clickedStn.openPopup()
}



// ==================================For Events==============================================================
var events = {};

function addEvent(event_id, df_id, row_index, latitude, longitude, a_color, p_color) {
    var marker = new L.CircleMarker(
        L.latLng(latitude, longitude), {
            radius: 10,
            color: "Black"
    }).bindPopup(event_id).on("click", eventClick);

    marker.status = "--";

    marker.myCustomEventID = event_id;
    marker.myCustomDfID = df_id;
    marker.myCustomRowID = row_index;

    map.addLayer(marker);

    events[event_id] = {
        "marker": marker,
        "latitude": latitude,
        "longitude": longitude,
        "active_color": a_color,
        "passive_color": p_color};

    setMarkerInactive(events[event_id]);
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


if(typeof MainWindow != 'undefined') {
    function eventClick(e) {
        var clickedEvent = e.target;

    //    highlightEvent(clickedCircle.myCustomID)
        clickedEvent.openPopup();
        MainWindow.onMap_marker_selected(clickedEvent.myCustomDfID, clickedEvent.myCustomRowID)
    }
}

