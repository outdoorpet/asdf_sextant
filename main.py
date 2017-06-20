#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Graphical user interface for Instaseis.

:copyright:
    Lion Krischer (krischer@geophysik.uni-muenchen.de), 2013-2014
:license:
    GNU Lesser General Public License, Version 3 [non-commercial/academic use]
    (http://www.gnu.org/copyleft/lgpl.html)
"""
from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

from PyQt4 import QtGui, QtCore, QtWebKit, QtNetwork
import pyqtgraph as pg
import qdarkstyle

from collections import defaultdict

from glob import iglob
import imp
import inspect
import itertools
import os
import sys
import tempfile
from os.path import join, exists

import obspy.core.event
import pyasdf
from pyasdf.exceptions import ASDFValueError

from obspy.core import UTCDateTime, Stream
from obspy.geodetics import gps2dist_azimuth, kilometer2degrees
from obspy.taup import TauPyModel

from DateAxisItem import DateAxisItem
from seisds import SeisDB

from sqlalchemy import create_engine, text, Column, Integer, String, or_, and_
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# Enums only exists in Python 3 and we don't really need them here...
STATION_VIEW_ITEM_TYPES = {
    "FILE": 0,
    "NETWORK": 1,
    "STATION": 2,
    "CHANNEL": 3,
    "STN_INFO": 4,
    "CHAN_INFO": 5}

EVENT_VIEW_ITEM_TYPES = {
    "EVENT": 0,
    "ORIGIN": 1,
    "MAGNITUDE": 2,
    "FOCMEC": 3}

AUX_DATA_ITEM_TYPES = {
    "DATA_TYPE": 0,
    "DATA_ITEM": 1}


# Default to antialiased drawing.
pg.setConfigOptions(antialias=True, foreground=(200, 200, 200),
                    background=None)


Base = declarative_base()

# Class for SQLite database for wavefoms belonging to station
class Waveforms(Base):
    __tablename__ = 'waveforms'
    # Here we define columns for the SQL table
    starttime = Column(Integer)
    endtime = Column(Integer)
    station_id = Column(String(250), nullable=False)
    tag = Column(String(250), nullable=False)
    full_id = Column(String(250), nullable=False, primary_key=True)

def compile_and_import_ui_files():
    """
    Automatically compiles all .ui files found in the same directory as the
    application py file.
    They will have the same name as the .ui files just with a .py extension.

    Needs to be defined in the same file as function loading the gui as it
    modifies the globals to be able to automatically import the created py-ui
    files. Its just very convenient.
    """
    directory = os.path.dirname(os.path.abspath(
        inspect.getfile(inspect.currentframe())))
    for filename in iglob(os.path.join(directory, '*.ui')):
        ui_file = filename
        py_ui_file = os.path.splitext(ui_file)[0] + os.path.extsep + 'py'
        if not os.path.exists(py_ui_file) or \
                (os.path.getmtime(ui_file) >= os.path.getmtime(py_ui_file)):
            from PyQt4 import uic
            print("Compiling ui file: %s" % ui_file)
            with open(py_ui_file, 'w') as open_file:
                uic.compileUi(ui_file, open_file)
        # Import the (compiled) file.
        try:
            import_name = os.path.splitext(os.path.basename(py_ui_file))[0]
            globals()[import_name] = imp.load_source(import_name, py_ui_file)
        except ImportError as e:
            print("Error importing %s" % py_ui_file)
            print(e.message)

def sizeof_fmt(num):
    """
    Handy formatting for human readable filesize.

    From http://stackoverflow.com/a/1094933/1657047
    """
    for x in ["bytes", "KB", "MB", "GB"]:
        if num < 1024.0 and num > -1024.0:
            return "%3.1f %s" % (num, x)
        num /= 1024.0
    return "%3.1f %s" % (num, "TB")

class timeDialog(QtGui.QDialog):
    def __init__(self, parent=None, ph_start=None, ph_end=None):
        QtGui.QDialog.__init__(self, parent)
        self.timeui = extract_time_dialog.Ui_ExtractTimeDialog()
        self.timeui.setupUi(self)

        self.timeui.starttime.setDateTime(QtCore.QDateTime.fromString(ph_start, "yyyy-MM-ddThh:mm:ss"))
        self.timeui.endtime.setDateTime(QtCore.QDateTime.fromString(ph_end, "yyyy-MM-ddThh:mm:ss"))

    def getValues(self):
        return (UTCDateTime(self.timeui.starttime.dateTime().toPyDateTime()),
                UTCDateTime(self.timeui.endtime.dateTime().toPyDateTime()))

class selectionDialog(QtGui.QDialog):
    '''
    Select all functionality is modified from Brendan Abel & dbc from their
    stackoverflow communication Feb 24th 2016:
    http://stackoverflow.com/questions/35611199/creating-a-toggling-check-all-checkbox-for-a-listview
    '''
    def __init__(self, parent=None, sta_list=None):
        QtGui.QDialog.__init__(self, parent)
        self.selui = select_stacomp_dialog.Ui_SelectDialog()
        self.selui.setupUi(self)

        # Set all check box to checked
        self.selui.check_all.setChecked(True)
        self.selui.check_all.clicked.connect(self.selectAllCheckChanged)

        self.model = QtGui.QStandardItemModel(self.selui.StaListView)

        self.sta_list = sta_list
        for sta in self.sta_list:
            item = QtGui.QStandardItem(sta)
            item.setCheckable(True)

            self.model.appendRow(item)

        self.selui.StaListView.setModel(self.model)
        self.selui.StaListView.clicked.connect(self.listviewCheckChanged)

    def selectAllCheckChanged(self):
        ''' updates the listview based on select all checkbox '''
        model = self.selui.StaListView.model()
        for index in range(model.rowCount()):
            item = model.item(index)
            if item.isCheckable():
                if self.selui.check_all.isChecked():
                    item.setCheckState(QtCore.Qt.Checked)
                else:
                    item.setCheckState(QtCore.Qt.Unchecked)

    def listviewCheckChanged(self):
        ''' updates the select all checkbox based on the listview '''
        model = self.selui.StaListView.model()
        items = [model.item(index) for index in range(model.rowCount())]

        if all(item.checkState() == QtCore.Qt.Checked for item in items):
            self.selui.check_all.setTristate(False)
            self.selui.check_all.setCheckState(QtCore.Qt.Checked)
        elif any(item.checkState() == QtCore.Qt.Checked for item in items):
            self.selui.check_all.setTristate(True)
            self.selui.check_all.setCheckState(QtCore.Qt.PartiallyChecked)
        else:
            self.selui.check_all.setTristate(False)
            self.selui.check_all.setCheckState(QtCore.Qt.Unchecked)



    def getSelected(self):
        select_stations = []
        i = 0
        while self.model.item(i):
            if self.model.item(i).checkState():
                select_stations.append(str(self.model.item(i).text()))
            i += 1

        # Return Selected stations and checked components
        return(select_stations, [self.selui.zcomp.isChecked(),
               self.selui.ncomp.isChecked(),
               self.selui.ecomp.isChecked()])

class Window(QtGui.QMainWindow):
    def __init__(self):
        QtGui.QMainWindow.__init__(self)
        # Injected by the compile_and_import_ui_files() function.
        self.ui = asdf_sextant_window.Ui_MainWindow()  # NOQA
        self.ui.setupUi(self)

        self.provenance_list_model = QtGui.QStandardItemModel(
            self.ui.provenance_list_view)
        self.ui.provenance_list_view.setModel(self.provenance_list_model)

        # Station view.
        map_file = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "resources/index.html"))
        self.ui.web_view.load(QtCore.QUrl.fromLocalFile(map_file))
        # Enable debugging of the web view.
        self.ui.web_view.settings().setAttribute(
            QtWebKit.QWebSettings.DeveloperExtrasEnabled, True)

        # Event view.
        map_file = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "resources/index_event.html"))
        self.ui.events_web_view.load(QtCore.QUrl.fromLocalFile(map_file))
        # Enable debugging of the web view.
        self.ui.events_web_view.settings().setAttribute(
            QtWebKit.QWebSettings.DeveloperExtrasEnabled, True)


        self._state = {}

        self.ui.openASDF.triggered.connect(self.open_asdf_file)
        self.ui.openJSON_DB.triggered.connect(self.open_json_file)
        # self.ui.bpfilter.triggered.connect(self.bpfilter)

        # Add right clickability to station view
        self.ui.station_view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.ui.station_view.customContextMenuRequested.connect(self.station_view_rightClicked)

        # Add right clickability to event view
        self.ui.event_tree_widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.ui.event_tree_widget.customContextMenuRequested.connect(self.event_tree_widget_rightClicked)

        QtGui.QApplication.instance().focusChanged.connect(self.changed_widget_focus)

        tmp = tempfile.mkstemp("asdf_sextant")
        os.close(tmp[0])
        try:
            os.remove(tmp[1])
        except:
            pass
        self._tempfile = tmp[1] + ".svg"

    def __del__(self):
        try:
            os.remove(self._tempfile)
        except:
            pass

    def __connect_signal_and_slots(self):
        """
        Connect special signals and slots not covered by the named signals and
        slots from pyuic4.
        """
        self.ui.station_view.itemEntered.connect(
            self.on_station_view_itemEntered)
        self.ui.station_view.itemExited.connect(
            self.on_station_view_itemExited)

    def changed_widget_focus(self):
        if QtGui.QApplication.focusWidget() == self.ui.graph:
            # Access the state dictionary and iterate through all stations in graph then highlight statins on web view
            for station_id in self._state["station_id"]:
                sta = station_id.split('.')[0] + '.' + station_id.split('.')[1]
                # Run Java Script to highlight all selected stations in station view
                js_call = "highlightStation('{station}')".format(station=sta)
                self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)

    def build_event_tree_view(self):
        if not hasattr(self, "ds") or not self.ds:
            return
        self.events = self.ds.events
        self.ui.event_tree_widget.clear()

        items = []
        self._state["quake_ids"] = {}

        for event in self.events:
            if event.origins:
                org = event.preferred_origin() or event.origins[0]

                js_call = "addEvent('{event_id}', {latitude}, {longitude});"\
                    .format(event_id=event.resource_id.id,
                            latitude=org.latitude,
                            longitude=org.longitude)
                self.ui.events_web_view.page().mainFrame().evaluateJavaScript(
                    js_call)

            event_item = QtGui.QTreeWidgetItem(
                [event.resource_id.id],
                type=EVENT_VIEW_ITEM_TYPES["EVENT"])
            self._state["quake_ids"][event.resource_id.id] = event_item

            origin_item = QtGui.QTreeWidgetItem(["Origins"], type=-1)
            magnitude_item = QtGui.QTreeWidgetItem(["Magnitudes"], type=-1)
            focmec_item = QtGui.QTreeWidgetItem(["Focal Mechanisms"], type=-1)

            org_items = []
            for origin in event.origins:
                org_items.append(
                    QtGui.QTreeWidgetItem(
                        [origin.resource_id.id],
                        type=EVENT_VIEW_ITEM_TYPES["ORIGIN"]))
                self._state["quake_ids"][origin.resource_id.id] = org_items[-1]
            origin_item.addChildren(org_items)

            mag_items = []
            for magnitude in event.magnitudes:
                mag_items.append(
                    QtGui.QTreeWidgetItem(
                        [magnitude.resource_id.id],
                        type=EVENT_VIEW_ITEM_TYPES["MAGNITUDE"]))
                self._state["quake_ids"][magnitude.resource_id.id] = \
                    mag_items[-1]
            magnitude_item.addChildren(mag_items)

            focmec_items = []
            for focmec in event.focal_mechanisms:
                focmec_items.append(
                    QtGui.QTreeWidgetItem(
                        [focmec.resource_id.id],
                        type=EVENT_VIEW_ITEM_TYPES["FOCMEC"]))
                self._state["quake_ids"][focmec.resource_id.id] = \
                    focmec_items[-1]
            focmec_item.addChildren(focmec_items)

            event_item.addChildren([origin_item, magnitude_item, focmec_item])
            items.append(event_item)

        self.ui.event_tree_widget.insertTopLevelItems(0, items)

    def build_station_view_list(self):
        if not hasattr(self, "ds") or not self.ds:
            return
        self.ui.station_view.clear()

        items = []

        # Iterate through station accessors in ASDF file
        for key, group in itertools.groupby(
                self.ds.waveforms,
                key=lambda x: x._station_name.split(".")[0]):
            network_item = QtGui.QTreeWidgetItem(
                [key],
                type=STATION_VIEW_ITEM_TYPES["NETWORK"])
            group = sorted(group, key=lambda x: x._station_name)

            # set with unique channel codes for station
            channel_codes_set = set()

            # Add all children stations.
            for station in sorted(group, key=lambda x: x._station_name):
                station_item = QtGui.QTreeWidgetItem([
                    station._station_name.split(".")[-1]],
                    type=STATION_VIEW_ITEM_TYPES["STATION"])

                # get stationxml (to channel level) for station
                station_inv = station.StationXML[0][0]

                # add info children
                station_children = [
                    QtGui.QTreeWidgetItem(['StartDate: \t%s' % station_inv.start_date.strftime('%Y-%m-%dT%H:%M:%S')],
                                          type=STATION_VIEW_ITEM_TYPES["STN_INFO"]),
                    QtGui.QTreeWidgetItem(['EndDate: \t%s' % station_inv.end_date.strftime('%Y-%m-%dT%H:%M:%S')],
                                          type=STATION_VIEW_ITEM_TYPES["STN_INFO"]),
                    QtGui.QTreeWidgetItem(['Latitude: \t%s' % station_inv.latitude],
                                          type=STATION_VIEW_ITEM_TYPES["STN_INFO"]),
                    QtGui.QTreeWidgetItem(['Longitude: \t%s' % station_inv.longitude],
                                          type=STATION_VIEW_ITEM_TYPES["STN_INFO"]),
                    QtGui.QTreeWidgetItem(['Elevation: \t%s' % station_inv.elevation],
                                          type=STATION_VIEW_ITEM_TYPES["STN_INFO"])]

                station_item.addChildren(station_children)

                # add channel items
                for channel_inv in station_inv:
                    # add the channel code to list
                    channel_codes_set.add(channel_inv.code)

                    channel_item = QtGui.QTreeWidgetItem(
                        [channel_inv.code], type=STATION_VIEW_ITEM_TYPES["CHANNEL"])

                    channel_children = [
                        QtGui.QTreeWidgetItem(
                            ['StartDate: \t%s' % station_inv.start_date.strftime('%Y-%m-%dT%H:%M:%S')],
                            type=STATION_VIEW_ITEM_TYPES["CHAN_INFO"]),
                        QtGui.QTreeWidgetItem(['EndDate: \t%s' % station_inv.end_date.strftime('%Y-%m-%dT%H:%M:%S')],
                                              type=STATION_VIEW_ITEM_TYPES["CHAN_INFO"]),
                        QtGui.QTreeWidgetItem(['Location: \t%s' % channel_inv.location_code],
                                              type=STATION_VIEW_ITEM_TYPES["CHAN_INFO"]),
                        QtGui.QTreeWidgetItem(['SamplRate: \t%s' % channel_inv.sample_rate],
                                              type=STATION_VIEW_ITEM_TYPES["CHAN_INFO"]),
                        QtGui.QTreeWidgetItem(['Azimuth: \t%s' % channel_inv.azimuth],
                                              type=STATION_VIEW_ITEM_TYPES["CHAN_INFO"]),
                        QtGui.QTreeWidgetItem(['Dip: \t%s' % channel_inv.dip],
                                              type=STATION_VIEW_ITEM_TYPES["CHAN_INFO"]),
                        QtGui.QTreeWidgetItem(['Latitude: \t%s' % channel_inv.latitude],
                                              type=STATION_VIEW_ITEM_TYPES["CHAN_INFO"]),
                        QtGui.QTreeWidgetItem(['Longitude: \t%s' % channel_inv.longitude],
                                              type=STATION_VIEW_ITEM_TYPES["CHAN_INFO"]),
                        QtGui.QTreeWidgetItem(['Elevation: \t%s' % channel_inv.elevation],
                                              type=STATION_VIEW_ITEM_TYPES["CHAN_INFO"])]

                    channel_item.addChildren(channel_children)

                    station_item.addChild(channel_item)

                network_item.addChild(station_item)
        items.append(network_item)

        self.ui.station_view.insertTopLevelItems(0, items)

    def on_initial_view_push_button_released(self):
        self.reset_view()

    def show_provenance_for_id(self, prov_id):
        try:
            info = \
                self.ds.provenance.get_provenance_document_for_id(prov_id)
        except ASDFValueError as e:
            msg_box = QtGui.QMessageBox()
            msg_box.setText(e.args[0])
            msg_box.exec_()
            return

        # Find the item.
        item = self.provenance_list_model.findItems(info["name"])[0]
        index = self.provenance_list_model.indexFromItem(item)
        self.ui.provenance_list_view.setCurrentIndex(index)
        self.show_provenance_document(info["name"])
        self.ui.central_tab.setCurrentWidget(self.ui.provenance_tab)

    def show_referenced_object(self, object_type, object_id):
        if object_type.lower() == "provenance":
            self.show_provenance_for_id(object_id)
        else:
            self.show_event(attribute=object_type.lower(), object_id=object_id)

    def show_event(self, attribute, object_id):
        item = self._state["quake_ids"][object_id]
        self.ui.event_tree_widget.collapseAll()
        self.ui.event_tree_widget.setCurrentItem(item)

        self.on_event_tree_widget_itemClicked(item, 0)

        self.ui.central_tab.setCurrentWidget(self.ui.event_tab)

    def on_show_auxiliary_provenance_button_released(self):
        if "current_auxiliary_data_provenance_id" not in self._state or \
                not self._state["current_auxiliary_data_provenance_id"]:
            return
        self.show_provenance_for_id(
            self._state["current_auxiliary_data_provenance_id"])

    def on_references_push_button_released(self):
        if "current_station_object" not in self._state:
            return
        obj = self._state["current_station_object"]

        popup = QtGui.QMenu()

        for waveform in obj.list():
            if not waveform.endswith(
                    "__" + self._state["current_waveform_tag"]):
                continue
            menu = popup.addMenu(waveform)
            attributes = dict(
                self.ds._waveform_group[obj._station_name][waveform].attrs)

            for key, value in sorted([_i for _i in attributes.items()],
                                     key=lambda x: x[0]):
                if not key.endswith("_id"):
                    continue
                key = key[:-3].capitalize()

                try:
                    value = value.decode()
                except:
                    pass

                def get_action_fct():
                    _key = key
                    _value = value

                    def _action(check):
                        self.show_referenced_object(_key, _value)

                    return _action

                # Bind with a closure.
                menu.addAction("%s: %s" % (key, value)).triggered.connect(
                    get_action_fct())

        popup.exec_(self.ui.references_push_button.parentWidget().mapToGlobal(
                    self.ui.references_push_button.pos()))

    # def create_asdf_sql(self, sta):
    #     # Function to separate the waveform string into seperate fields
    #     def waveform_sep(ws):
    #         a = ws.split('__')
    #         starttime = int(UTCDateTime(a[1].encode('ascii')).timestamp)
    #         endtime = int(UTCDateTime(a[2].encode('ascii')).timestamp)
    #
    #         # Returns: (station_id, starttime, endtime, waveform_tag)
    #         return (ws.encode('ascii'), a[0].encode('ascii'), starttime, endtime, a[3].encode('ascii'))
    #
    #     # Get the SQL file for station
    #     SQL_filename = r""+os.path.dirname(self.filename)+ '/' + str(sta.split('.')[1]) + '.db'
    #
    #     check_SQL = exists(SQL_filename)
    #
    #     if check_SQL:
    #         return
    #     # need to create SQL database
    #     elif not check_SQL:
    #         # Initialize (open/create) the sqlalchemy sqlite engine
    #         engine = create_engine('sqlite:///' + SQL_filename)
    #         Session = sessionmaker()
    #
    #         # Get list of all waveforms for station
    #         waveforms_list = self.ds.waveforms[str(sta)].list()
    #         #remove the station XML file
    #         waveforms_list.remove('StationXML')
    #
    #         # Create all tables in the engine
    #         Base.metadata.create_all(engine)
    #
    #         # Initiate a session with the SQL database so that we can add data to it
    #         Session.configure(bind=engine)
    #         session = Session()
    #
    #         progressDialog = QtGui.QProgressDialog("Building SQL Library for Station {0}".format(str(sta)),
    #                                                "Cancel", 0, len(waveforms_list))
    #
    #         # go through the waveforms (ignore stationxml file)
    #         for _i, sta_wave in enumerate(waveforms_list):
    #             progressDialog.setValue(_i)
    #
    #             # The ASDF formatted waveform name for SQL [full_id, station_id, starttime, endtime, tag]
    #             waveform_info = waveform_sep(sta_wave)
    #
    #             # create new SQL entry
    #             new_wave_SQL = Waveforms(full_id=waveform_info[0], station_id=waveform_info[1],
    #                                      starttime=waveform_info[2],
    #                                      endtime=waveform_info[3], tag=waveform_info[4])
    #
    #             # Add the waveform info to the session
    #             session.add(new_wave_SQL)
    #             session.commit()

    def open_json_file(self):
        self.db_filename = str(QtGui.QFileDialog.getOpenFileName(
            parent=self, caption="Choose JSON Database File",
            directory=os.path.expanduser(os.path.dirname(self.asdf_filename)),
            filter="JSON Database File (*.json)"))
        if not self.db_filename:
            return

        print('')
        print("Initializing Database..")

        # create the seismic database
        self.seisdb = SeisDB(json_file=self.db_filename)

        print("Seismic Database Initilized!")

    def open_asdf_file(self):
        """
        Fill the station tree widget upon opening a new file.
        """
        self.asdf_filename = str(QtGui.QFileDialog.getOpenFileName(
            parent=self, caption="Choose File",
            directory=os.path.expanduser("~"),
            filter="ASDF files (*.h5)"))
        if not self.asdf_filename:
            return

        self.ds = pyasdf.ASDFDataSet(self.asdf_filename)

        for station_id, coordinates in self.ds.get_all_coordinates().items():
            if not coordinates:
                continue
            js_call = "addStation('{station_id}', {latitude}, {longitude})"
            self.ui.web_view.page().mainFrame().evaluateJavaScript(
                js_call.format(station_id=station_id,
                               latitude=coordinates["latitude"],
                               longitude=coordinates["longitude"]))

        self.build_station_view_list()
        self.build_event_tree_view()

        # Add all the provenance items
        self.provenance_list_model.clear()
        for provenance in self.ds.provenance.list():
            item = QtGui.QStandardItem(provenance)
            self.provenance_list_model.appendRow(item)

        # Also add the auxiliary data.

        def recursive_tree(name, item):
            if isinstance(item, pyasdf.utils.AuxiliaryDataAccessor):
                data_type_item = QtGui.QTreeWidgetItem(
                    [name],
                    type=AUX_DATA_ITEM_TYPES["DATA_TYPE"])
                children = []
                for sub_item in item.list():
                    children.append(recursive_tree(sub_item, item[sub_item]))
                data_type_item.addChildren(children)
            elif isinstance(item, pyasdf.utils.AuxiliaryDataContainer):
                data_type_item = QtGui.QTreeWidgetItem(
                    [name],
                    type=AUX_DATA_ITEM_TYPES["DATA_ITEM"])
            else:
                raise NotImplementedError
            return data_type_item

        items = []
        for data_type in self.ds.auxiliary_data.list():
            items.append(recursive_tree(data_type,
                                        self.ds.auxiliary_data[data_type]))
        self.ui.auxiliary_data_tree_view.insertTopLevelItems(0, items)

        sb = self.ui.status_bar
        if hasattr(sb, "_widgets"):
            for i in sb._widgets:
                sb.removeWidget(i)

        w = QtGui.QLabel("File: %s    (%s)" % (self.ds.filename,
                                               self.ds.pretty_filesize))
        sb._widgets = [w]
        sb.addPermanentWidget(w)
        w.show()
        sb.show()
        sb.reformat()

    def on_detrend_and_demean_check_box_stateChanged(self, state):
        self.update_waveform_plot()

    def on_normalize_check_box_stateChanged(self, state):
        self.update_waveform_plot()

    def on_group_by_network_check_box_stateChanged(self, state):
        self.build_station_view_list()

    # def bpfilter(self):
    #     self.bpfilter_selected = True
    #     self.update_waveform_plot()

    def on_graph_itemClicked(self, event):
        if event.button() == 4:
            items = self.ui.graph.scene().items(event.scenePos())
            sel_plot = [x for x in items if isinstance(x, pg.PlotItem)][0]
            pos = QtCore.QPointF(event.scenePos())

            vLine = pg.InfiniteLine(angle=90, movable=True)
            sel_plot.addItem(vLine, ignoreBounds=True)

            vb = sel_plot.vb
            if sel_plot.sceneBoundingRect().contains(pos):
                mousePoint = vb.mapSceneToView(pos)
                vLine.setPos(mousePoint.x())

    def update_waveform_plot(self):
        self.ui.central_tab.setCurrentIndex(0)
        self.ui.initial_view_push_button.setEnabled(True)
        self.ui.previous_view_push_button.setEnabled(True)
        self.ui.previous_interval_push_button.setEnabled(True)
        self.ui.next_interval_push_button.setEnabled(True)

        # Get the filter settings.
        filter_settings = {}
        filter_settings["detrend_and_demean"] = \
            self.ui.detrend_and_demean_check_box.isChecked()
        filter_settings["normalize"] = self.ui.normalize_check_box.isChecked()
        #filter_settings["bpfilter"] = self.bdfilter_selected

        temp_st = self.st.copy()

        if filter_settings["detrend_and_demean"]:
            temp_st.detrend("linear")
            temp_st.detrend("demean")

        if filter_settings["normalize"]:
            temp_st.normalize()

        # if filter_settings["bpfilter"]:
        #     temp_st.filter("bandpass",freqmin = 0.01, freqmax = 10)

        self.ui.graph.clear()
        self.ui.graph.setMinimumPlotHeight(200)

        starttimes = []
        endtimes = []
        min_values = []
        max_values = []

        self._state["waveform_plots"] = []
        self._state["station_id"] = []
        self._state["station_tag"] = []
        for _i, tr in enumerate(temp_st):
            plot = self.ui.graph.addPlot(
                _i, 0, title=tr.id,
                axisItems={'bottom': DateAxisItem(orientation='bottom',
                                                  utcOffset=0)})
            plot.show()
            self._state["waveform_plots"].append(plot)
            self._state["station_id"].append(tr.stats.network+'.'+
                                               tr.stats.station+'.'+
                                               tr.stats.location+'.'+
                                               tr.stats.channel)
            self._state["station_tag"].append(str(tr.stats.asdf.tag))
            plot.plot(tr.times() + tr.stats.starttime.timestamp, tr.data)
            starttimes.append(tr.stats.starttime)
            endtimes.append(tr.stats.endtime)
            min_values.append(tr.data.min())
            max_values.append(tr.data.max())

            vLine = pg.InfiniteLine(angle=90, movable=True)
            plot.addItem(vLine, ignoreBounds=True)

            plot.scene().sigMouseClicked.connect(self.on_graph_itemClicked)

        self._state["waveform_plots_min_time"] = min(starttimes)
        self._state["waveform_plots_max_time"] = max(endtimes)
        self._state["waveform_plots_min_value"] = min(min_values)
        self._state["waveform_plots_max_value"] = max(max_values)

        for plot in self._state["waveform_plots"][1:]:
            plot.setXLink(self._state["waveform_plots"][0])
            plot.setYLink(self._state["waveform_plots"][0])

        self.reset_view()

    def on_previous_interval_push_button_released(self):
        # Get start and end time of previous interval with 10% overlap
        starttime = UTCDateTime(self._state["waveform_plots_min_time"])
        endtime = UTCDateTime(self._state["waveform_plots_max_time"])

        delta_time = endtime - starttime
        overlap_time = delta_time * 0.1

        self.new_start_time = starttime - (delta_time - overlap_time)
        self.new_end_time = starttime + overlap_time

        self.extract_from_continuous(True, st_ids=self._state["station_id"],
                                     st_tags=self._state["station_tag"])

    def on_next_interval_push_button_released(self):
        # Get start and end time of next interval with 10% overlap
        starttime = UTCDateTime(self._state["waveform_plots_min_time"])
        endtime = UTCDateTime(self._state["waveform_plots_max_time"])

        delta_time = endtime - starttime
        overlap_time = delta_time * 0.1

        self.new_start_time = endtime - (overlap_time)
        self.new_end_time = endtime + (delta_time - overlap_time)

        self.extract_from_continuous(True, st_ids = self._state["station_id"],
                                     st_tags = self._state["station_tag"])

    def reset_view(self):
        self._state["waveform_plots"][0].setXRange(
            self._state["waveform_plots_min_time"].timestamp,
            self._state["waveform_plots_max_time"].timestamp)
        min_v = self._state["waveform_plots_min_value"]
        max_v = self._state["waveform_plots_max_value"]

        y_range = max_v - min_v
        min_v -= 0.1 * y_range
        max_v += 0.1 * y_range
        self._state["waveform_plots"][0].setYRange(min_v, max_v)

    def show_provenance_document(self, document_name):
        doc = self.ds.provenance[document_name]
        doc.plot(filename=self._tempfile, use_labels=True)

        self.ui.provenance_graphics_view.open_file(self._tempfile)

    def on_station_view_itemClicked(self, item, column):
        t = item.type()

        def get_station(item):
            station = item.text(0)
            if "." not in station:
                station = item.parent().text(0) + "." + station
            return station

        if t == STATION_VIEW_ITEM_TYPES["NETWORK"]:
            pass
        elif t == STATION_VIEW_ITEM_TYPES["STATION"]:
            station = get_station(item)
        # elif t == STATION_VIEW_ITEM_TYPES["STATIONXML"]:
        #     station = get_station(item.parent())
        #     self.ds.waveforms[station].StationXML.plot()#plot_response(0.001)
        # elif t == STATION_VIEW_ITEM_TYPES["WAVEFORM"]:
        #     station = get_station(item.parent())
        #     # self._state["current_station_object"] = self.ds.waveforms[station]
        #     # self._state["current_waveform_tag"] = item.text(0)
        #     # self.st = self.ds.waveforms[station][str(item.text(0))]
        #     # self.update_waveform_plot()
        else:
            pass

    def station_view_rightClicked(self, position):
        item = self.ui.station_view.selectedItems()[0]

        t = item.type()

        def get_station(item):
            station = item.text(0)
            if "." not in station:
                station = item.parent().text(0) + "." + station
            return station

        if t == STATION_VIEW_ITEM_TYPES["NETWORK"]:
            self.net_item_menu = QtGui.QMenu(self)
            ext_menu = QtGui.QMenu('Select NSLC', self)
        # elif t == STATION_VIEW_ITEM_TYPES["STATIONXML"]:
        #     pass
        # elif t == STATION_VIEW_ITEM_TYPES["WAVEFORM"]:
        #     pass
        elif t == STATION_VIEW_ITEM_TYPES["STATION"]:
            station = get_station(item)
            # make sure JSON DB is loaded in
            if not self.seisdb:
                print("No DB is Loaded!!")
                return

            wave_tag_list = self.ds.waveforms[station].get_waveform_tags()

            # Run Method to create ASDF SQL database with SQLite (one db per station within ASDF)
            # self.create_asdf_sql(station)

            self.sta_item_menu = QtGui.QMenu(self)
            ext_menu = QtGui.QMenu('Extract Time Interval', self)

            # Add actions for each tag for station
            for wave_tag in wave_tag_list:
                action = QtGui.QAction(wave_tag, self)
                # Connect the triggered menu object to a function passing an extra variable
                action.triggered.connect(lambda: self.extract_from_continuous(False, sta=station, wave_tag=wave_tag))
                ext_menu.addAction(action)

            self.sta_item_menu.addMenu(ext_menu)

            self.action = self.sta_item_menu.exec_(self.ui.station_view.viewport().mapToGlobal(position))

    def on_event_tree_widget_itemClicked(self, item, column):
        t = item.type()
        if t not in EVENT_VIEW_ITEM_TYPES.values():
            return

        text = str(item.text(0))

        res_id = obspy.core.event.ResourceIdentifier(id=text)

        obj = res_id.get_referred_object()
        if obj is None:
            self.events = self.ds.events
        self.ui.events_text_browser.setPlainText(
            str(res_id.get_referred_object()))

        if t == EVENT_VIEW_ITEM_TYPES["EVENT"]:
            event = text
        elif t == EVENT_VIEW_ITEM_TYPES["ORIGIN"]:
            event = str(item.parent().parent().text(0))
        elif t == EVENT_VIEW_ITEM_TYPES["MAGNITUDE"]:
            event = str(item.parent().parent().text(0))
        elif t == EVENT_VIEW_ITEM_TYPES["FOCMEC"]:
            event = str(item.parent().parent().text(0))

        js_call = "highlightEvent('{event_id}');".format(event_id=event)
        print(js_call)
        self.ui.events_web_view.page().mainFrame().evaluateJavaScript(js_call)

    def event_tree_widget_rightClicked(self, position):
        item = self.ui.event_tree_widget.selectedItems()[0]

        t = item.type()
        if t not in EVENT_VIEW_ITEM_TYPES.values():
            return
        text = str(item.text(0))
        res_id = obspy.core.event.ResourceIdentifier(id=text)

        obj = res_id.get_referred_object()
        if obj is None:
            self.events = self.ds.events
        self.ui.events_text_browser.setPlainText(
            str(res_id.get_referred_object()))

        if t == EVENT_VIEW_ITEM_TYPES["EVENT"]:
            event = text
        elif t == EVENT_VIEW_ITEM_TYPES["ORIGIN"]:
            event = str(item.parent().parent().text(0))
        elif t == EVENT_VIEW_ITEM_TYPES["MAGNITUDE"]:
            event = str(item.parent().parent().text(0))
        elif t == EVENT_VIEW_ITEM_TYPES["FOCMEC"]:
            event = str(item.parent().parent().text(0))

        self.event_item_menu = QtGui.QMenu(self)

        action = QtGui.QAction('Plot Event', self)
        # Connect the triggered menu object to a function passing an extra variable
        action.triggered.connect(lambda: self.analyse_earthquake(obj))
        self.event_item_menu.addAction(action)

        # ext_menu = QtGui.QMenu('Extract Time Interval', self)
        #
        # # Add actions for each tag for station
        # for wave_tag in wave_tag_list:
        #     action = QtGui.QAction(wave_tag, self)
        #     # Connect the triggered menu object to a function passing an extra variable
        #     action.triggered.connect(lambda: self.extract_from_continuous(False, sta=station, wave_tag=wave_tag))
        #     ext_menu.addAction(action)
        #
        # self.event_item_menu.addMenu(ext_menu)
        #


        self.action = self.event_item_menu.exec_(self.ui.station_view.viewport().mapToGlobal(position))

    def on_auxiliary_data_tree_view_itemClicked(self, item, column):
        t = item.type()
        if t != AUX_DATA_ITEM_TYPES["DATA_ITEM"]:
            return

        tag = str(item.text(0))

        def recursive_path(item):
            p = item.parent()
            if p is None:
                return []
            path = [str(p.text(0))]
            path.extend(recursive_path(p))
            return path

        # Find the full path.
        path = recursive_path(item)
        path.reverse()

        graph = self.ui.auxiliary_data_graph
        graph.clear()

        group = self.ds.auxiliary_data["/".join(path)]
        aux_data = group[tag]

        if len(aux_data.data.shape) == 1 and path[0] != "Files":
            plot = graph.addPlot(title="%s/%s" % ("/".join(path), tag))
            plot.show()
            plot.plot(aux_data.data.value)
            self.ui.auxiliary_data_stacked_widget.setCurrentWidget(
                self.ui.auxiliary_data_graph_page)
        # Files are a bit special.
        elif len(aux_data.data.shape) == 1 and path[0] == "Files":
            self.ui.auxiliary_file_browser.setPlainText(
                aux_data.file.read().decode())
            self.ui.auxiliary_data_stacked_widget.setCurrentWidget(
                self.ui.auxiliary_data_file_page)
        # 2D Shapes.
        elif len(aux_data.data.shape) == 2:
            img = pg.ImageItem(border="#3D8EC9")
            img.setImage(aux_data.data.value)
            vb = graph.addViewBox()
            vb.setAspectLocked(True)
            vb.addItem(img)
            self.ui.auxiliary_data_stacked_widget.setCurrentWidget(
                self.ui.auxiliary_data_graph_page)
        # Anything else is currently not supported.
        else:
            raise NotImplementedError

        # Show the parameters.
        tv = self.ui.auxiliary_data_detail_table_view
        tv.clear()

        self._state["current_auxiliary_data_provenance_id"] = \
            aux_data.provenance_id
        if aux_data.provenance_id:
            self.ui.show_auxiliary_provenance_button.setEnabled(True)
        else:
            self.ui.show_auxiliary_provenance_button.setEnabled(False)

        tv.setRowCount(len(aux_data.parameters))
        tv.setColumnCount(2)
        tv.setHorizontalHeaderLabels(["Parameter", "Value"])
        tv.horizontalHeader().setResizeMode(QtGui.QHeaderView.Stretch)
        tv.verticalHeader().hide()

        for _i, key in enumerate(sorted(aux_data.parameters.keys())):
            key_item = QtGui.QTableWidgetItem(key)
            value_item = QtGui.QTableWidgetItem(str(aux_data.parameters[key]))

            tv.setItem(_i, 0, key_item)
            tv.setItem(_i, 1, value_item)

        # Show details about the data.
        details = [
            ("shape", str(aux_data.data.shape)),
            ("dtype", str(aux_data.data.dtype)),
            ("dimensions", str(len(aux_data.data.shape))),
            ("uncompressed size", sizeof_fmt(
                aux_data.data.dtype.itemsize * aux_data.data.size))]

        tv = self.ui.auxiliary_data_info_table_view
        tv.clear()

        tv.setRowCount(len(details))
        tv.setColumnCount(2)
        tv.setHorizontalHeaderLabels(["Attribute", "Value"])
        tv.horizontalHeader().setResizeMode(QtGui.QHeaderView.Stretch)
        tv.verticalHeader().hide()

        for _i, item in enumerate(details):
            key_item = QtGui.QTableWidgetItem(item[0])
            value_item = QtGui.QTableWidgetItem(item[1])

            tv.setItem(_i, 0, key_item)
            tv.setItem(_i, 1, value_item)

    def on_provenance_list_view_clicked(self, model_index):
        # Compat for different pyqt/sip versions.
        try:
            data = str(model_index.data().toString())
        except:
            data = str(model_index.data())

        self.show_provenance_document(data)

    def on_station_view_itemEntered(self, item):
        t = item.type()

        def get_station(item, parent=True):
            if parent:
                station = str(item.parent().text(0))
                if "." not in station:
                    station = item.parent().parent().text(0) + "." + station
            else:
                station = item.text(0)
                if "." not in station:
                    station = item.parent().text(0) + "." + station
            return station

        if t == STATION_VIEW_ITEM_TYPES["NETWORK"]:
            network = item.text(0)
            js_call = "highlightNetwork('{network}')".format(network=network)
            self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)
        elif t == STATION_VIEW_ITEM_TYPES["STATION"]:
            station = get_station(item, parent=False)
            js_call = "highlightStation('{station}')".format(station=station)
            self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)
        elif t == STATION_VIEW_ITEM_TYPES["CHANNEL"]:
            station = get_station(item)
            js_call = "highlightStation('{station}')".format(station=station)
            self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)
        elif t == STATION_VIEW_ITEM_TYPES["CHAN_INFO"]:
            station = get_station(item)
            js_call = "highlightStation('{station}')".format(station=station)
            self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)
        elif t == STATION_VIEW_ITEM_TYPES["STN_INFO"]:
            station = get_station(item)
            js_call = "highlightStation('{station}')".format(station=station)
            self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)
        else:
            pass

    def on_station_view_itemExited(self, *args):
        js_call = "setAllInactive()"
        self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)

    def query_sql_db(self, query, sql_filename, sta):
        # Open a new st object
        st = Stream()

        # Initialize (open/create) the sqlalchemy sqlite engine
        engine = create_engine('sqlite:///' + sql_filename)
        Session = sessionmaker()
        Session.configure(bind=engine)
        session = Session()

        for matched_waveform in session.query(Waveforms).filter(query):
            st += self.ds.waveforms[sta][matched_waveform.full_id]

        return(st)

    def extract_from_continuous(self, override, **kwargs):
        # Open a new st object
        self.st = Stream()


        # If override flag then we are calling this
        # method by using prev/next interval buttons
        if override:
            print(kwargs["st_ids"])
            net = str(kwargs["st_ids"][0]).split('.')[0]
            sta = str(kwargs["st_ids"][0]).split('.')[1]
            stnxml = self.ds.waveforms[net+'.'+sta].StationXML
            # get the channels for that station
            xml_list = stnxml.get_contents()['channels']
            rec_start = stnxml[0][0].start_date

            chan_list = []

            for id in xml_list:
                chan = id.split('.')[-1]
                chan_list.append(str(chan))

            interval_tuple = (self.new_start_time.timestamp, self.new_end_time.timestamp)
            query = self.seisdb.queryByTime([sta], chan_list, interval_tuple[0],
                                            interval_tuple[1])

            for matched_info in query.values():
                print(matched_info["ASDF_tag"])

                # read the data from the ASDF into stream
                temp_tr = self.ds.waveforms[net+'.'+sta][matched_info["ASDF_tag"]][0]

                # trim trace to start and endtime
                temp_tr.trim(starttime=UTCDateTime(interval_tuple[0]), endtime=UTCDateTime(interval_tuple[1]))

                # append trace to stream
                self.st += temp_tr

                # free memory
                temp_tr = None



        elif not override:
            stnxml = self.ds.waveforms[kwargs['sta']].StationXML
            # get the channels for that station
            xml_list = stnxml.get_contents()['channels']
            rec_start = stnxml[0][0].start_date

            chan_list = []

            for id in xml_list:
                chan = id.split('.')[-1]
                chan_list.append(str(chan))
            # Launch the custom extract time dialog
            dlg = timeDialog(self, ph_start=str(rec_start).split('.')[0], ph_end=str(rec_start + 60*60).split('.')[0])
            if dlg.exec_():
                values = dlg.getValues()
                interval_tuple = (values[0].timestamp, values[1].timestamp)

                print(interval_tuple)


                print('---------------------------------------')
                print('Finding Data for specified time interval.....')


                query = self.seisdb.queryByTime([str(kwargs['sta']).split('.')[1]], chan_list, interval_tuple[0], interval_tuple[1])

                for matched_info in query.values():
                    print(matched_info["ASDF_tag"])

                    # read the data from the ASDF into stream
                    temp_tr = self.ds.waveforms[kwargs['sta']][matched_info["ASDF_tag"]][0]

                    # trim trace to start and endtime
                    temp_tr.trim(starttime=UTCDateTime(interval_tuple[0]), endtime=UTCDateTime(interval_tuple[1]))

                    # append trace to stream
                    self.st += temp_tr

                    # free memory
                    temp_tr = None

        if self.st.__nonzero__():
            # Attempt to merge all traces with matching ID'S in place
            print('')
            print('Merging Traces from %s Stations....' % len(self.st))
            self.st.merge()
            print('\nTrimming Traces to specified time interval....')
            self.st.trim(starttime=UTCDateTime(interval_tuple[0]), endtime=UTCDateTime(interval_tuple[1]))
            self.update_waveform_plot()
        else:
            msg = QtGui.QMessageBox()
            msg.setIcon(QtGui.QMessageBox.Critical)
            msg.setText("No Data for Requested Time Interval")
            msg.setDetailedText("There are no waveforms to display for selected time interval:"
                                "\nStart Time = "+str(UTCDateTime(interval_tuple[0],precision=0))+
                                "\nEnd Time =   "+str(UTCDateTime(interval_tuple[1],precision=0)))
            msg.setWindowTitle("Extract Time Error")
            msg.setStandardButtons(QtGui.QMessageBox.Ok)
            msg.exec_()

    def analyse_earthquake(self, event_obj):
        # Get event catalogue
        self.event_cat = self.ds.events
        comp_list = ['*Z', '*N', '*E']


        # Launch the custom station/component selection dialog
        sel_dlg = selectionDialog(parent=self, sta_list=self.ds.waveforms.list())
        if sel_dlg.exec_():
            select_sta, bool_comp = sel_dlg.getSelected()
            query_comp = list(itertools.compress(comp_list, bool_comp))

            # Open up a new stream object
            self.st = Stream()

            # use the ifilter functionality to extract desired streams to visualize
            for station in self.ds.ifilter(self.ds.q.station == map(lambda el: el.split('.')[1], select_sta),
                                           self.ds.q.channel == query_comp,
                                           self.ds.q.event == event_obj):
                for filtered_id in station.list():
                    if filtered_id == 'StationXML':
                        continue
                    self.st += station[filtered_id]

            if self.st.__nonzero__():
                print(self.st)
                # Get quake origin info
                origin_info = event_obj.preferred_origin() or event_obj.origins[0]

                # Iterate through traces
                for tr in self.st:
                    # Run Java Script to highlight all selected stations in station view
                    js_call = "highlightStation('{station}')".format(station=tr.stats.network + '.' +tr.stats.station)
                    self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)


                    # Get inventory for trace
                    inv = self.ds.waveforms[tr.stats.network + '.' +tr.stats.station].StationXML
                    sta_coords = inv.get_coordinates(tr.get_id())

                    dist, baz, _ = gps2dist_azimuth(sta_coords['latitude'],
                                                    sta_coords['longitude'],
                                                    origin_info.latitude,
                                                    origin_info.longitude)
                    dist_deg = kilometer2degrees(dist/1000.0)
                    tt_model = TauPyModel(model='iasp91')
                    arrivals = tt_model.get_travel_times(origin_info.depth/1000.0, dist_deg, ('P'))

                    # Write info to trace header
                    tr.stats.distance = dist
                    tr.stats.ptt = arrivals[0]

                # Sort the st by distance from quake
                self.st.sort(keys=['distance'])


                self.update_waveform_plot()



def launch():
    # Automatically compile all ui files if they have been changed.
    compile_and_import_ui_files()

    # Launch and open the window.
    app = QtGui.QApplication(sys.argv, QtGui.QApplication.GuiClient)
    app.setStyleSheet(qdarkstyle.load_stylesheet(pyside=False))
    window = Window()

    # Move window to center of screen.
    window.move(
        app.desktop().screen().rect().center() - window.rect().center())

    # Show and bring window to foreground.
    window.show()
    app.installEventFilter(window)
    window.raise_()
    ret_val = app.exec_()
    window.__del__()
    os._exit(ret_val)


if __name__ == "__main__":

    # proxy = raw_input("Proxy:")
    # port = raw_input("Proxy Port:")
    #
    # networkProxy = QtNetwork.QNetworkProxy(QtNetwork.QNetworkProxy.HttpProxy, proxy, int(port))
    # QtNetwork.QNetworkProxy.setApplicationProxy(networkProxy)
    launch()
