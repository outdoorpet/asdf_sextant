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

from PyQt4 import QtGui, QtCore, QtWebKit, QtNetwork, uic
import pyqtgraph as pg
import qdarkstyle

import pandas as pd
import numpy as np

import glob
import itertools
import os
import sys
import tempfile
import obspy.core.event
import pyasdf
from pyasdf.exceptions import ASDFValueError
import functools
import math
import inspect
import importlib
import json
import random

from distutils.util import strtobool

from os.path import join, dirname, basename
from obspy.core import UTCDateTime, Stream, Trace
from obspy import read_events
from obspy.clients.fdsn.client import Client
from obspy.clients.fdsn.header import FDSNException
# from obspy.signal import filter
from DateAxisItem import DateAxisItem
from seisds import SeisDB
from query_input_yes_no import query_yes_no

from obspy.geodetics import gps2dist_azimuth, kilometer2degrees
from obspy.taup import TauPyModel

from MyMultiPlotWidget import MyMultiPlotWidget
from asdf_rf_calc_MPI import asdf_rf_calc
from clean_rf_output import clean_rf_ds

# TODO: Add in ability to multiplot in auxillary data view
# TODO: add in scroll bar to plot window when there are too many plots (like QC_P_time_compare)
# TODO: fix Mac OS QMenu bar (currnetly the app needs to be de-focussed and focussed to make the menu bar work)
# TODO: test functionality with ASDF file with multiple networks
# TODO: add functionality to highlight logfile associated with a waveform
# TODO: investigate making application into exectuable
# TODO: Find out why its so slow to load in big ASDF files (see below)
"""
               - Discovered that it is opening the auxillary info and the station xml associated
                 with each waveform that is slow.
               - made it so that only the upper levels of the Auxillary data are read on
                 start-up and lower down data is read on the fly
               - Also modified station information population to be on the fly below station level down
               - Now using the JSON database to find unique channels and tags in asdf file
"""

# load in Qt Designer UI files
asdf_sextant_window_ui = "asdf_sextant_window.ui"
select_stacomp_dialog_ui = "selection_dialog.ui"
extract_time_dialog_ui = "extract_time_dialog.ui"
eq_extraction_dialog_ui = "eq_extraction_dialog.ui"
data_avail_dialog_ui = "data_avail_dialog.ui"
filter_dialog = "filter_dialog.ui"
residual_set_limit_ui = "residual_set_limit.ui"

Ui_MainWindow, QtBaseClass = uic.loadUiType(asdf_sextant_window_ui)
Ui_SelectDialog, QtBaseClass = uic.loadUiType(select_stacomp_dialog_ui)
Ui_ExtractTimeDialog, QtBaseClass = uic.loadUiType(extract_time_dialog_ui)
Ui_EqExtractionDialog, QtBaseClass = uic.loadUiType(eq_extraction_dialog_ui)
Ui_DataAvailDialog, QtBaseClass = uic.loadUiType(data_avail_dialog_ui)
Ui_FilterDialog, QtBaseClass = uic.loadUiType(filter_dialog)
Ui_ResDialog, QtBaseClass = uic.loadUiType(residual_set_limit_ui)

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


class PandasModel(QtCore.QAbstractTableModel):
    """
    Class to populate a table view with a pandas dataframe
    """

    def __init__(self, data, cat_nm=None, trace_nm=None, pick_nm=None, parent=None):
        QtCore.QAbstractTableModel.__init__(self, parent)
        self._data = np.array(data.values)
        self._cols = data.columns
        self.r, self.c = np.shape(self._data)

        self.cat_nm = cat_nm
        self.trace_nm = trace_nm
        self.pick_nm = pick_nm

        # Column headers for tables
        self.cat_col_header = ['Event ID', 'Time (UTC Timestamp)', 'Lat (dd)', 'Lon  (dd)',
                               'Depth (km)', 'Mag', 'Time (UTC)', 'Julian Day']
        self.trace_col_header = ["ASDF ID", "ID", "Channel", "Trace Start (UTC)", "Trace End (UTC)",
                                 "Trace Start Timestamp (UTC)", "Trace End Timestamp (UTC)", "Trace ASDF Tag"]
        self.pick_col_header = ['Event ID', 'Station', 'Arr Time Residual (s)', 'P Arr Time (UTC)',
                                'P_as Arr Time (UTC)']

    def rowCount(self, parent=None):
        return self.r

    def columnCount(self, parent=None):
        return self.c

    def data(self, index, role=QtCore.Qt.DisplayRole):

        if index.isValid():
            if role == QtCore.Qt.DisplayRole:
                return self._data[index.row(), index.column()]
        return None

    def headerData(self, p_int, orientation, role):
        if role == QtCore.Qt.DisplayRole:
            if orientation == QtCore.Qt.Horizontal:
                if not self.cat_nm == None:
                    return self.cat_col_header[p_int]
                elif not self.trace_nm == None:
                    return self.trace_col_header[p_int]
                elif not self.pick_nm == None:
                    return self.pick_col_header[p_int]
            elif orientation == QtCore.Qt.Vertical:
                return p_int
        return None


class TraceTableDialog(QtGui.QDialog):
    """
      Class to create a separate child window to display the traces for a sttaion on a table
      """

    # TODO: add in sortable header
    def __init__(self, parent=None, trace_df=None):
        super(TraceTableDialog, self).__init__(parent)

        self.trace_df = trace_df

        self.initUI()

    def initUI(self):
        self.layout = QtGui.QVBoxLayout(self)

        self.trace_table_view = QtGui.QTableView()

        self.trace_table_view.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)

        self.layout.addWidget(self.trace_table_view)

        self.setLayout(self.layout)

        # Populate the tables using the custom Pandas table class
        self.trace_model = PandasModel(self.trace_df, trace_nm=True)

        self.trace_table_view.setModel(self.trace_model)

        self.setWindowTitle('Trace Table')
        self.show()


class EqTableDialog(QtGui.QDialog):
    """
    Class to create a separate child window to display the event catalogue and a map
    """

    # TODO: fix station highlight on map for EQ Table Dialog
    # TODO: save the view state including zoom etc..
    def __init__(self, parent=None, cat_df=None):
        QtGui.QDialog.__init__(self, parent)
        self.tbldui = Ui_EqExtractionDialog()
        self.tbldui.setupUi(self)

        self.cat_df = cat_df

        self.tbldui.EQ_xtract_tableView.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)

        # Populate the tables using the custom Pandas table class
        cat_model = PandasModel(self.cat_df, cat_nm=True)

        self.tbldui.EQ_xtract_tableView.setModel(cat_model)

        cache = QtNetwork.QNetworkDiskCache()
        cache.setCacheDirectory("cache")
        self.tbldui.EQ_xtract_webView.page().networkAccessManager().setCache(cache)
        self.tbldui.EQ_xtract_webView.page().networkAccessManager()

        self.tbldui.EQ_xtract_webView.page().mainFrame().addToJavaScriptWindowObject("EqTableDialog", self)
        self.tbldui.EQ_xtract_webView.page().setLinkDelegationPolicy(QtWebKit.QWebPage.DelegateAllLinks)
        self.tbldui.EQ_xtract_webView.load(QtCore.QUrl('resources/map.html'))
        self.tbldui.EQ_xtract_webView.loadFinished.connect(self.onLoadFinished)
        self.tbldui.EQ_xtract_webView.linkClicked.connect(QtGui.QDesktopServices.openUrl)

        self.show()

        # self.plot_events()

    @QtCore.pyqtSlot(str, int)
    def onMap_marker_selected(self, event_id, row_index):
        print(event_id, row_index)
        # self.selected_file = os.path.basename(self.root_asdf_filename)
        # self.station_view_itemClicked(self.ASDF_accessor[self.selected_file]['file_tree_item'])
        # self.table_view_highlight(self.tbl_view_dict[str(df_id)], row_index)

    def onLoadFinished(self):
        with open('resources/map.js', 'r') as f:
            frame = self.tbldui.EQ_xtract_webView.page().mainFrame()
            frame.evaluateJavaScript(f.read())
            # frame.evaluateJavaScript("test();")
            for row_index, row in self.cat_df.iterrows():
                # js_call = "test();"
                js_call = "addEvent('{event_id}', {row_index}, " \
                          "{latitude}, {longitude}, '{a_color}', '{p_color}');" \
                    .format(event_id=row['event_id'], row_index=int(row_index), latitude=row['lat'],
                            longitude=row['lon'], a_color="Red",
                            p_color="#008000")

                print(js_call)
                frame.evaluateJavaScript(js_call)

                # def plot_events(self):
                #     # Plot the events
                #     with open('resources/map.js', 'r') as f:
                #         frame = self.tbldui.EQ_xtract_webView.page().mainFrame()
                #         frame.evaluateJavaScript(f.read())
                #
                #         for row_index, row in self.cat_df.iterrows():
                #             js_call = "test();"
                #             # js_call = "addEvent('{event_id}', {row_index}, " \
                #             #           "{latitude}, {longitude}, '{a_color}', '{p_color}');" \
                #             #     .format(event_id=row['event_id'], row_index=int(row_index), latitude=row['lat'],
                #             #             longitude=row['lon'], a_color="Red",
                #             #             p_color="#008000")
                #
                #             print(js_call)
                #             frame.evaluateJavaScript(js_call)


class PickTableDialog(QtGui.QDialog):
    """
    Class to create a separate child window to display the event catalogue and picks
    """

    def __init__(self, parent=None, cat_df=None, pick_df=None):
        super(PickTableDialog, self).__init__(parent)

        self.cat_df = cat_df
        self.pick_df = pick_df

        self.initUI()

    def initUI(self):
        self.layout = QtGui.QVBoxLayout(self)

        self.cat_event_table_view = QtGui.QTableView()
        self.pick_table_view = QtGui.QTableView()

        self.cat_event_table_view.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)
        self.pick_table_view.setSelectionBehavior(QtGui.QAbstractItemView.SelectRows)

        self.layout.addWidget(self.cat_event_table_view)
        self.layout.addWidget(self.pick_table_view)

        self.setLayout(self.layout)

        # Populate the tables using the custom Pandas table class
        self.cat_model = PandasModel(self.cat_df, cat_nm=True)
        self.pick_model = PandasModel(self.pick_df, pick_nm=True)

        self.cat_event_table_view.setModel(self.cat_model)
        self.pick_table_view.setModel(self.pick_model)

        self.setWindowTitle('EQ Catalogue and Picks Tables')
        self.show()


class DataAvailPlot(QtGui.QDialog):
    '''
    Dialog for Data Availablity plot
    '''

    def __init__(self, parent=None, net_list=None, sta_list=None, chan_list=None, tags_list=None,
                 rec_int_dict=None, cat_avail=False, cat_df=None):
        QtGui.QDialog.__init__(self, parent)
        self.davailui = Ui_DataAvailDialog()
        self.davailui.setupUi(self)
        self.davailui.go_push_button.setEnabled(False)

        self.cat_df = cat_df
        self.cat_avail = cat_avail

        # self.data_avail_graph_view = pg.GraphicsLayoutWidget()

        self.rec_int_dict = rec_int_dict
        self.net_list = net_list
        self.sta_list = sta_list
        self.chan_list = chan_list
        self.tags_list = tags_list

        self.select_data()
        self.plot_data()

        # flag for which extract region method is used
        self.xtract_method = None

        self.show()

    def roi_tooltip(self, sta_id, sta, roi_type):
        if roi_type == "reg1":
            self.plot.setToolTip("XCOR region 1")
        else:
            self.plot.setToolTip("XCOR region 2")

    def control_roi_translate(self, roi, active, sta_id):
        """
        Keep the roi so that it's y axis is always along the station it is associated with
        :param roi: roi that is being moved
        :param active: (bool) if the user is still moving the ROI or if finished
        :param sta_id: id number (int) of the station that the roi is associated with
        """
        if active:
            if roi.pos()[1] != sta_id - 0.3:
                roi.setPos(pg.Point(roi.pos()[0], sta_id - 0.3), update=False)
        else:
            # prevent all ROI movement
            pass

    def display_plot_view_region(self, start, end):
        # save the plot view state
        self.saved_state = self.plot.getViewBox().getState()

        # reset the plot
        self.plot_data()
        self.lri = pg.LinearRegionItem(
            values=[start, end], brush='r', movable=False)

        self.plot.addItem(self.lri)
        self.davailui.go_push_button.setEnabled(False)

    def on_sel_xcor_regions_push_button_released(self):
        self.davailui.go_push_button.setEnabled(True)

        self.xtract_method = "xcor_region"

        # reset the plot
        self.plot_data()

        # get the view range of the plot window [[xmin, xmax],[ymin, ymax]]
        vr = self.plot.viewRange()

        # dictionary for ROI access
        self.roi_dict = {}

        for key, sta_id in self.sta_id_dict.iteritems():
            bef_roi_pen = QtGui.QPen()
            bef_roi_pen.setColor(QtCore.Qt.green)

            bef_roi = pg.ROI(pos=[vr[0][0] + (60 * 60 * 24), sta_id - 0.3], size=[60 * 60 * 24, 0.6], pen=bef_roi_pen)
            bef_roi.addScaleHandle([0, 0.5], [0.5, 0.5])
            bef_roi.setZValue(0)

            aft_roi_pen = QtGui.QPen()
            aft_roi_pen.setColor(QtCore.Qt.yellow)
            aft_roi = pg.ROI(pos=[vr[0][1] - (60 * 60 * 24 * 2), sta_id - 0.3], size=[60 * 60 * 24, 0.6],
                             pen=aft_roi_pen)
            aft_roi.addScaleHandle([1, 0.5], [0.5, 0.5])
            aft_roi.setZValue(0)

            self.plot.addItem(bef_roi)
            self.plot.addItem(aft_roi)

            # make popup tooltip about the roi
            bef_roi.sigHoverEvent.connect(functools.partial(self.roi_tooltip, sta_id, key, "bef"))
            aft_roi.sigHoverEvent.connect(functools.partial(self.roi_tooltip, sta_id, key, "aft"))

            # control where the roi can be dragged too
            bef_roi.sigRegionChanged.connect(functools.partial(self.control_roi_translate, bef_roi, True, sta_id))
            aft_roi.sigRegionChanged.connect(functools.partial(self.control_roi_translate, aft_roi, True, sta_id))

            # add rois to dict
            self.roi_dict[sta_id] = {"bef": bef_roi, "aft": aft_roi}

    def on_sel_view_region_push_button_released(self):
        """
        Method to create a single Linear region for extracting data for all stations in view
        """
        self.davailui.go_push_button.setEnabled(True)
        self.xtract_method = "view_region"

        # get the view range of the plot window [[xmin, xmax],[ymin, ymax]]
        vr = self.plot.viewRange()

        # dictionary for ROI access
        self.roi_dict = {}

        self.plot_data()
        self.lri = pg.LinearRegionItem(
            values=[vr[0][0] + (60 * 60 * 24 * 40), vr[0][0] + (60 * 60 * 24 * 40) + (60 * 60 * 2)])

        self.plot.addItem(self.lri)

    def dispMousePos(self, pos):

        # Display current mouse coords if over the scatter plot area as a tooltip
        try:
            x_coord = UTCDateTime(self.plot.vb.mapSceneToView(pos).toPoint().x()).ctime()
            # print(self.plot.vb.mapSceneToView(pos).toPoint().x())
            # if self.plot.vb.mapSceneToView(pos).toPoint().x() in self.cat_df["qtime"].tolist():
            # print("QUAKE")
            # print(self.plot.vb.mapSceneToView(pos).toPoint().x())
            self.time_tool = self.plot.setToolTip(x_coord)
        except:
            pass

    def select_data(self):

        # enum_sta = list(enumerate(self.rec_int_dict.keys()))
        enum_sta = list(enumerate(self.rec_int_dict.keys()))

        # rearrange dict
        self.sta_id_dict = dict([(b, a) for a, b in enum_sta])

        self.y_axis_string = pg.AxisItem(orientation='left')
        self.y_axis_string.setTicks([enum_sta])

        # # Launch the custom station/component selection dialog
        # sel_dlg = selectionDialog(parent=self, net_list=self.net_list, sta_list=self.sta_list, chan_list=self.chan_list,
        #                           tags_list=self.tags_list)
        # if sel_dlg.exec_():
        #     self.select_net, self.select_sta, self.select_comp, self.select_tags = sel_dlg.getSelected()
        #
        #     # new list of stations nn.sssss format with only those in selected stations
        #     net_sta_list = []
        #
        #     for net_sta in self.rec_int_dict.keys():
        #         net = net_sta.split('.')[0]
        #         sta = net_sta.split('.')[1]
        #         if (net in self.select_net and sta in self.select_sta):
        #             net_sta_list.append(net_sta)
        #
        #     # enum_sta = list(enumerate(self.rec_int_dict.keys()))
        #     enum_sta = list(enumerate(net_sta_list))
        #
        #     # rearrange dict
        #     self.sta_id_dict = dict([(b, a) for a, b in enum_sta])
        #
        #     self.y_axis_string = pg.AxisItem(orientation='left')
        #     self.y_axis_string.setTicks([enum_sta])

    def plot_data(self):

        def get_sta_id(sta):
            return (self.sta_id_dict[sta])

        self.davailui.data_avail_graph_view.clear()

        # Set up the plotting area
        self.plot = self.davailui.data_avail_graph_view.addPlot(0, 0,
                                                                axisItems={'bottom': DateAxisItem(orientation='bottom',
                                                                                                  utcOffset=0),
                                                                           'left': self.y_axis_string})
        self.plot.setMouseEnabled(x=True, y=False)
        # When Mouse is moved over plot print the data coordinates
        self.plot.scene().sigMouseMoved.connect(self.dispMousePos)

        # Re-establish previous map_view_station if it exists
        if hasattr(self, "saved_state"):
            self.plot.getViewBox().setState(self.saved_state)

        rec_midpoints = []
        sta_ids = []
        diff_frm_mid_list = []

        # iterate through stations
        for stn_key, rec_array in self.rec_int_dict.iteritems():

            # if not stn_key.split('.')[1] in self.select_sta:
            #     continue

            # iterate through gaps list
            for _i in range(rec_array.shape[1]):
                diff_frm_mid = (rec_array[1, _i] - rec_array[0, _i]) / 2.0

                diff_frm_mid_list.append(diff_frm_mid)

                rec_midpoints.append(rec_array[0, _i] + diff_frm_mid)
                sta_ids.append(get_sta_id(stn_key))

        # Plot Error bar data recording intervals
        err = pg.ErrorBarItem(x=np.array(rec_midpoints), y=np.array(sta_ids), left=np.array(diff_frm_mid_list),
                              right=np.array(diff_frm_mid_list), beam=0.06)

        err.setZValue(10)

        self.plot.addItem(err)

        self.plot_earthquakes()

    def plot_earthquakes(self):
        # cant compare if data frame is none or not
        if self.cat_avail:
            # get the earthquake timestamps
            qtimes = self.cat_df["qtime"].tolist()
            ids = self.cat_df["event_id"].tolist()
            print(qtimes)

            for i, qtime in enumerate(qtimes):
                qline = pg.InfiniteLine(pos=qtime)
                self.plot.addItem(qline)

    # qline.sigMouseClicked.connect(self.quake_clicked)
    #
    #
    # def quake_clicked(self, pos):
    #     print('hi')

    def get_roi_data(self):
        print(self.xtract_method)

        def get_left_right_roi(r):
            """
            get the left and right (x) values for a roi
            :param r: roi
            :return: x left, x right
            """

            roi_left_x = r.pos()[0]
            roi_width = r.size()[0]

            return (roi_left_x, roi_left_x + roi_width)

        if self.xtract_method == "view_region":
            # make the region non moveable now
            self.lri.setMovable(False)
            # get the start and end time of extraction region and also return desired net/sta/chan and tags
            return (self.xtract_method, self.net_list, self.sta_list, self.chan_list, self.tags_list,
                    self.lri.getRegion())
        elif self.xtract_method == "xcor_region":
            roi_limits_dict = {}
            # go through all rois and get edges
            for key, sta_id in self.sta_id_dict.iteritems():
                # get the roi
                bef_roi, aft_roi = (self.roi_dict[sta_id]["bef"], self.roi_dict[sta_id]["aft"])

                # # set the movability to false now
                # bef_roi.sigRegionChanged.connect(functools.partial(self.control_roi_translate, bef_roi, False, sta_id))
                # aft_roi.sigRegionChanged.connect(functools.partial(self.control_roi_translate, aft_roi, False, sta_id))

                roi_limits_dict[key.split('.')[1]] = (get_left_right_roi(bef_roi), get_left_right_roi(aft_roi))
            return (
                self.xtract_method, self.net_list, self.sta_list, self.chan_list, self.tags_list, roi_limits_dict)
        else:
            # no extraction region
            return None


class selectionDialog(QtGui.QDialog):
    '''
    Select all functionality is modified from Brendan Abel & dbc from their
    stackoverflow communication Feb 24th 2016:
    http://stackoverflow.com/questions/35611199/creating-a-toggling-check-all-checkbox-for-a-listview
    '''

    def __init__(self, parent=None, net_list=None, sta_list=None, chan_list=None, tags_list=None, ph_start=None,
                 ph_end=None, xquake=None, gaps_analysis=False):
        QtGui.QDialog.__init__(self, parent)
        self.selui = Ui_SelectDialog()
        self.selui.setupUi(self)
        self.gaps_analysis = gaps_analysis
        # self.setWindowTitle('Selection Dialog')

        # Set all check box to checked
        # self.selui.check_all.setChecked(True)

        # calling the class where extraction time does not matter
        if xquake == None:
            self.no_time = True
            self.selui.starttime.setEnabled(False)
            self.selui.endtime.setEnabled(False)
            self.selui.asdf_output_checkBox.setEnabled(False)
            self.selui.refstn_output_checkBox.setEnabled(False)
            self.selui.bef_quake_spinBox.setEnabled(False)
            self.selui.aft_quake_spinBox.setEnabled(False)

        elif xquake == True:
            self.no_time = False
            self.selui.starttime.setEnabled(False)
            self.selui.endtime.setEnabled(False)
            if not ph_start == None and not ph_end == None:
                self.selui.starttime.setDateTime(QtCore.QDateTime.fromString(ph_start, "yyyy-MM-ddThh:mm:ss"))
                self.selui.endtime.setDateTime(QtCore.QDateTime.fromString(ph_end, "yyyy-MM-ddThh:mm:ss"))

        elif xquake == False:
            self.no_time = False
            self.selui.starttime.setDateTime(QtCore.QDateTime.fromString(ph_start, "yyyy-MM-ddThh:mm:ss"))
            self.selui.endtime.setDateTime(QtCore.QDateTime.fromString(ph_end, "yyyy-MM-ddThh:mm:ss"))
            self.selui.asdf_output_checkBox.setEnabled(False)
            self.selui.refstn_output_checkBox.setEnabled(False)
            self.selui.bef_quake_spinBox.setEnabled(False)
            self.selui.aft_quake_spinBox.setEnabled(False)

        self.selui.check_all.clicked.connect(self.selectAllCheckChanged)

        # -------- add networks to network select items
        self.net_model = QtGui.QStandardItemModel(self.selui.NetListView)

        self.net_list = net_list
        for net in self.net_list:
            item = QtGui.QStandardItem(net)
            item.setCheckable(True)

            if len(net_list) == 1:
                item.setCheckState(QtCore.Qt.Checked)

            self.net_model.appendRow(item)

        self.selui.NetListView.setModel(self.net_model)

        # -------- add stations to station select items
        self.sta_model = QtGui.QStandardItemModel(self.selui.StaListView)

        self.sta_list = sta_list
        for sta in self.sta_list:
            item = QtGui.QStandardItem(sta)
            item.setCheckable(True)

            if len(sta_list) == 1:
                item.setCheckState(QtCore.Qt.Checked)

            self.sta_model.appendRow(item)

        self.selui.StaListView.setModel(self.sta_model)
        # cnnect to method to update stae of select all checkbox
        self.selui.StaListView.clicked.connect(self.listviewCheckChanged)

        # -------- add channels to channel select items
        self.chan_model = QtGui.QStandardItemModel(self.selui.ChanListView)

        self.chan_list = chan_list
        for chan in self.chan_list:
            item = QtGui.QStandardItem(chan)
            item.setCheckable(True)
            if len(chan_list) == 1:
                item.setCheckState(QtCore.Qt.Checked)

            self.chan_model.appendRow(item)

        self.selui.ChanListView.setModel(self.chan_model)

        # -------- add ASDF tags to tags select items
        self.tags_model = QtGui.QStandardItemModel(self.selui.TagsListView)

        self.tags_list = tags_list
        for tags in self.tags_list:
            item = QtGui.QStandardItem(tags)
            item.setCheckable(True)
            if len(tags_list) == 1:
                item.setCheckState(QtCore.Qt.Checked)
            self.tags_model.appendRow(item)

        self.selui.TagsListView.setModel(self.tags_model)

        if self.gaps_analysis:
            # we are looking at station availability only make one of channels and one of the tags available for selection
            self.selui.ChanListView.clicked.connect(self.single_sel_chan)
            self.selui.TagsListView.clicked.connect(self.single_sel_tags)

    def single_sel_tags(self, index):
        """
        Uncheck all other channels except for one that was clicked on if we are analysing station availability
        We only want to look at the availability of one station at a time
        :return:
        """

        i = 0
        while self.tags_model.item(i):
            if not self.tags_model.item(i).text() == index.data().toString():
                self.tags_model.item(i).setCheckState(QtCore.Qt.Unchecked)
            i += 1

    def single_sel_chan(self, index):
        """
        Uncheck all other channels except for one that was clicked on if we are analysing station availability
        We only want to look at the availability of one station at a time
        :return:
        """

        i = 0
        while self.chan_model.item(i):
            if not self.chan_model.item(i).text() == index.data().toString():
                self.chan_model.item(i).setCheckState(QtCore.Qt.Unchecked)
            i += 1

    def selectAllCheckChanged(self):
        ''' updates the listview based on select all checkbox '''
        sta_model = self.selui.StaListView.model()
        for index in range(sta_model.rowCount()):
            item = sta_model.item(index)
            if item.isCheckable():
                if self.selui.check_all.isChecked():
                    item.setCheckState(QtCore.Qt.Checked)
                else:
                    item.setCheckState(QtCore.Qt.Unchecked)

    def listviewCheckChanged(self):
        ''' updates the select all checkbox based on the listview '''
        sta_model = self.selui.StaListView.model()
        items = [sta_model.item(index) for index in range(sta_model.rowCount())]

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
        select_networks = []
        select_stations = []
        select_channels = []
        select_tags = []
        i = 0
        while self.net_model.item(i):
            if self.net_model.item(i).checkState():
                select_networks.append(str(self.net_model.item(i).text()))
            i += 1
        i = 0
        while self.sta_model.item(i):
            if self.sta_model.item(i).checkState():
                select_stations.append(str(self.sta_model.item(i).text()))
            i += 1
        i = 0
        while self.chan_model.item(i):
            if self.chan_model.item(i).checkState():
                select_channels.append(str(self.chan_model.item(i).text()))
            i += 1
        i = 0
        while self.tags_model.item(i):
            if self.tags_model.item(i).checkState():
                select_tags.append(str(self.tags_model.item(i).text()))
            i += 1
        if self.no_time:
            # Return Selected networks, stations and selected channels, tags
            return (select_networks, select_stations, select_channels, select_tags)
        else:
            # Return Selected networks, stations and selected channels, tags and start and end times and
            # before quake and after quake extraction times(or defaults)
            return (select_networks, select_stations, select_channels, select_tags,
                    UTCDateTime(self.selui.starttime.dateTime().toPyDateTime()),
                    UTCDateTime(self.selui.endtime.dateTime().toPyDateTime()),
                    self.selui.asdf_output_checkBox.isChecked(),
                    self.selui.refstn_output_checkBox.isChecked(), self.selui.bef_quake_spinBox.value() * 60,
                    self.selui.aft_quake_spinBox.value() * 60)


class MyFilterTableModel(QtCore.QAbstractTableModel):
    def __init__(self, datain, parent=None):
        QtCore.QAbstractTableModel.__init__(self, parent)
        self.arraydata = datain

    def rowCount(self, parent):
        return len(self.arraydata)

    def columnCount(self, parent):
        return len(self.arraydata[0])

    def data(self, index, role):
        if not index.isValid():
            return None
        elif role != QtCore.Qt.DisplayRole:
            return None

        return (self.arraydata[index.row()][index.column()])

    def setData(self, index, value, role):
        self.arraydata[index.row()][index.column()] = value
        return True

    def flags(self, index):
        if not index.isValid():
            return None
        elif index.column() == 1:
            return QtCore.Qt.ItemIsEditable | QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        else:
            return QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable


class FilterDialog(QtGui.QDialog):
    """
    Class for dialog to select filter type and filter arguments to be applied to data in view
    """

    def __init__(self, parent=None):
        QtGui.QDialog.__init__(self, parent)
        self.filui = Ui_FilterDialog()
        self.filui.setupUi(self)

        # populate filter types
        filter_type_list = ["bandpass", "bandstop", "highpass",
                            "lowpass", "lowpass_cheby_2"]

        self.filter_type_model = QtGui.QStandardItemModel(self.filui.filter_type_listView)

        for filter_type in filter_type_list:
            item = QtGui.QStandardItem(filter_type)
            item.setCheckable(False)
            self.filter_type_model.appendRow(item)

        self.filui.filter_type_listView.setModel(self.filter_type_model)

    @QtCore.pyqtSlot(QtCore.QModelIndex)
    def on_filter_type_listView_clicked(self, index):
        # get the selected filter
        self.filter_sel = index.data().toString()

        # import the filter function from obspy
        fil_func = getattr(importlib.import_module("obspy.signal.filter"), str(self.filter_sel))

        # get the arguments of the selected filter with the data argument removed
        args_list = inspect.getargspec(fil_func).args
        defaults_list = [None for x in range(len(args_list))]

        # if the filter has defaults then populate them in the list
        if inspect.getargspec(fil_func).defaults is not None:
            for i, def_par in enumerate(inspect.getargspec(fil_func).defaults):
                index = i - len(inspect.getargspec(fil_func).defaults)
                defaults_list[index] = def_par

        # remove the data argument and the df argument
        new_args_list = []
        new_def_list = []

        for i, arg in enumerate(args_list):
            if not arg in ["data", "df"]:
                new_args_list.append(args_list[i])
                new_def_list.append(defaults_list[i])

        self.args_array = np.array([new_args_list, new_def_list]).transpose()

        if not self.args_array.shape[0] == 0:
            self.build_args_table()

    def build_args_table(self):
        self.tablemodel = MyFilterTableModel(self.args_array, self)
        self.filui.filter_args_tableView.setModel(self.tablemodel)

    def get_arguments(self):
        ret_args = []
        params = []

        if not self.args_array.shape[0] == 0:
            # get the argument values from the table
            for row in range(self.args_array.shape[0]):
                index = self.tablemodel.index(row, 1)
                params.append(str(self.tablemodel.data(self.tablemodel.index(row, 0), QtCore.Qt.DisplayRole)))
                try:
                    iter_arg = str(self.tablemodel.data(index, QtCore.Qt.DisplayRole).toString())
                except AttributeError:
                    try:
                        iter_arg = str(self.tablemodel.data(index, QtCore.Qt.DisplayRole).toFloat())
                    except AttributeError:
                        iter_arg = str(self.tablemodel.data(index, QtCore.Qt.DisplayRole))

                if iter_arg in ["false", "true", "True", "False"]:
                    iter_arg = strtobool(iter_arg)
                else:
                    iter_arg = float(iter_arg)

                ret_args.append(iter_arg)

        return [str(self.filter_sel), dict(zip(params, ret_args))]


class ResidualSetLimit(QtGui.QDialog):
    """
        Class to select a time residual limit
    """

    def __init__(self, parent=None):
        super(ResidualSetLimit, self).__init__(parent)
        self.resui = Ui_ResDialog()
        self.resui.setupUi(self)

    def getValues(self):
        ll_sec = float(self.resui.LL_min.value()) * 60 + float(self.resui.LL_sec.value())
        ul_sec = float(self.resui.UL_min.value()) * 60 + float(self.resui.UL_sec.value())

        return (ll_sec, ul_sec)


class SingleStnPlot(QtGui.QDialog):
    """
    Pop up window for plotting single station graph
    """

    def __init__(self, parent=None, picks_df_stn=None, col_list=None, x_axis_string=None, stn=None):
        super(SingleStnPlot, self).__init__(parent)

        self.picks_df_stn = picks_df_stn
        self.col_list = col_list
        self.x_axis_string = x_axis_string
        self.stn = stn

        self.initUI()

    def initUI(self):
        self.layout = QtGui.QVBoxLayout(self)

        self.single_stn_graph_view = pg.GraphicsLayoutWidget()
        self.layout.addWidget(self.single_stn_graph_view)

        self.update_stn_graph()

        self.show()

    def update_stn_graph(self):
        self.single_stn_graph_view.clear()

        # Set up the plotting area
        self.plot_area = self.single_stn_graph_view.addPlot(0, 0,
                                                            title="Time Difference: P Theoretical - P Picked "
                                                                  "for Station: " + self.stn,
                                                            axisItems={'bottom': self.x_axis_string})
        self.plot_area.setMouseEnabled(x=True, y=False)
        self.plot_area.setLabel('left', "TT Residual", units='s')
        self.plot_area.setLabel('bottom', "Event ID")

        # Plot midway scatter points between time diff
        self.time_diff_scatter_plot = pg.ScatterPlotItem(pxMode=True)
        # self.time_diff_scatter_plot.sigClicked.connect(self.scatter_point_clicked)
        self.time_diff_scatter_plot.addPoints(self.picks_df_stn['alt_midpoints'],
                                              self.picks_df_stn['tt_diff'], size=9,
                                              brush=self.col_list)
        self.plot_area.addItem(self.time_diff_scatter_plot)


class Window(QtGui.QMainWindow):
    def __init__(self):
        QtGui.QMainWindow.__init__(self)
        # Injected by the compile_and_import_ui_files() function.
        self.ui = Ui_MainWindow()  # NOQA
        self.ui.setupUi(self)

        self.__connect_signal_and_slots()

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

        # set up dictionary for different ASDF files and associated attributes/items
        self.ASDF_accessor = {}

        # set up dictioanry to access any tables in seperate windows
        self.table_accessor = {}

        # set up tuple for keys pressed
        self.modifier_key = ""

        # variable to keep track if arrivals have been modified or picked
        self.arrivals_modified = False

        # add in icon for reset waveform view button
        self.ui.reset_view_push_button.setIcon(QtGui.QIcon('eLsS8.png'))

        # disable buttons in the waveform plot view region
        self.ui.reset_view_push_button.setEnabled(False)
        self.ui.previous_interval_push_button.setEnabled(False)
        self.ui.next_interval_push_button.setEnabled(False)
        self.ui.sort_drop_down_button.setEnabled(False)
        self.ui.references_push_button.setEnabled(False)
        self.ui.detrend_and_demean_check_box.setEnabled(False)
        self.ui.normalize_check_box.setEnabled(False)
        self.ui.waveform_filter_check_box.setEnabled(False)

        # disable buttons in the p-time-analysis tab
        self.ui.sort_drop_down_button_2.setEnabled(False)
        self.ui.plot_single_stn_button.setEnabled(False)
        self.ui.gather_events_checkbox.setEnabled(False)

        # color bar for p-time analysis
        self.ui.col_grad_w.loadPreset('spectrum')
        self.ui.col_grad_w.setEnabled(False)
        self.ui.col_grad_w.setToolTip("""
                                - Click a triangle to change its color
                                - Drag triangles to move
                                - Click in an empty area to add a new color
                                - Right click a triangle to remove
                                """)

        # reset button for p-time analysis
        self.ui.pick_reset_view_button.setIcon(QtGui.QIcon('eLsS8.png'))
        self.ui.pick_reset_view_button.released.connect(self.reset_plot_view)
        self.ui.pick_reset_view_button.setToolTip("Reset the scatter plot zoom and sort method")

        # Open StreetMAP for station map in p-time-analysis tab
        station_map_cache = QtNetwork.QNetworkDiskCache()
        station_map_cache.setCacheDirectory("station_map_cache")
        self.ui.map_view_station.page().networkAccessManager().setCache(station_map_cache)
        self.ui.map_view_station.page().networkAccessManager()

        self.ui.map_view_station.page().mainFrame().addToJavaScriptWindowObject("MainWindow", self)
        self.ui.map_view_station.page().setLinkDelegationPolicy(QtWebKit.QWebPage.DelegateAllLinks)
        self.ui.map_view_station.load(QtCore.QUrl('map.html'))
        self.ui.map_view_station.loadFinished.connect(self.onLoadFinished)
        self.ui.map_view_station.linkClicked.connect(QtGui.QDesktopServices.openUrl)

        # Open StreetMAP for events map in p-time-analysis tab
        events_map_cache = QtNetwork.QNetworkDiskCache()
        events_map_cache.setCacheDirectory("events_map_cache")
        self.ui.map_view_events.page().networkAccessManager().setCache(events_map_cache)
        self.ui.map_view_events.page().networkAccessManager()

        self.ui.map_view_events.page().mainFrame().addToJavaScriptWindowObject("MainWindow", self)
        self.ui.map_view_events.page().setLinkDelegationPolicy(QtWebKit.QWebPage.DelegateAllLinks)
        self.ui.map_view_events.load(QtCore.QUrl('map.html'))
        self.ui.map_view_events.loadFinished.connect(self.onLoadFinished)
        self.ui.map_view_events.linkClicked.connect(QtGui.QDesktopServices.openUrl)

        # use a custom multiplot class to create waveform plotting region (allows scrollbar and minimum height of plots)
        self.waveform_graph = MyMultiPlotWidget()

        self.ui.graph_stackedWidget.addWidget(self.waveform_graph)
        self.ui.graph_stackedWidget.setCurrentWidget(self.waveform_graph)

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

    def onLoadFinished(self):
        with open('map.js', 'r') as f:
            station_frame = self.ui.map_view_station.page().mainFrame()
            station_frame.evaluateJavaScript(f.read())
            events_frame = self.ui.map_view_events.page().mainFrame()
            events_frame.evaluateJavaScript(f.read())

    def closeEvent(self, QCloseEvent):
        # necessary to ensure data is written into ASDF file
        try:

            # check if asdf files have been modified and if so ask weather to save
            if self.arrivals_modified:
                quit_msg = "ASDF files have been modified.\nDo you want to SAVE?"
                reply = QtGui.QMessageBox.question(self, 'Save Message',
                                                   quit_msg, QtGui.QMessageBox.Yes, QtGui.QMessageBox.No)

                if reply == QtGui.QMessageBox.Yes:
                    print("Closing and saving ASDF data")
                    # close all datasets
                    for value in self.ASDF_accessor.values():
                        # print(value["ds"].auxiliary_data.ArrivalData["10408504"]["OA_CD27_0M_HHZ"])
                        # print(value["ds"])
                        del value["ds"]

                    del self.ds
                else:
                    pass

        except AttributeError:
            # there is no loaded in ASDF data
            pass

    def __connect_signal_and_slots(self):
        """
        Connect special signals and slots not covered by the named signals and
        slots from pyuic4.
        """
        # QMenu items connect slots
        self.ui.openASDF.triggered.connect(self.open_asdf_file)
        self.ui.openEQ_QuakeML.triggered.connect(self.open_EQ_cat)
        self.ui.actionStation_Availability.triggered.connect(self.station_availability)
        self.ui.actionCalc_Receiver_Function.triggered.connect(lambda: self.calculate_RF(None))

        # Buttons on p-time Analysis tab
        self.ui.plot_single_stn_button.released.connect(self.plot_single_stn_selected)
        self.ui.gather_events_checkbox.stateChanged.connect(self.gather_events_checkbox_changed)
        self.ui.analyse_p_pushButton.released.connect(self.analyse_p_time)

        # waveform options button
        self.ui.waveform_filter_settings_toolButton.released.connect(self.waveform_filter_settings)

        # picker tool
        # self.ui.pick_arrivals_tool_button.released.connect(self.ASDF_arrival_picker)

        self.ui.station_view.itemEntered.connect(
            self.on_station_view_itemEntered)
        self.ui.station_view.itemExited.connect(
            self.on_station_view_itemExited)

    def change_active_ASDF(self, ds_id):
        print("Changing Active ASDF file....")
        self.ds = self.ASDF_accessor[ds_id]["ds"]
        self.db = self.ASDF_accessor[ds_id]["db"]
        self.ds_id = ds_id

        self.read_ASDF_info()

    def changed_widget_focus(self):
        if QtGui.QApplication.focusWidget() == self.waveform_graph:
            # Access the state dictionary and iterate through all stations in graph then highlight statins on web view
            try:
                for station_id in self._state["station_id"]:
                    sta = station_id.split('.')[0] + '.' + station_id.split('.')[1]
                    # Run Java Script to highlight all selected stations in station view
                    js_call = "highlightStation('{station}')".format(station=sta)
                    self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)

            except KeyError:
                # there are no stations loaded in
                pass

    def build_pick_tables(self):
        # self.table_accessor = None

        if self.ui.gather_events_checkbox.isChecked():
            # drop some columns from the dataframes
            dropped_picks_df = self.picks_df.drop(['col_val', 'sta_id', 'time_mid', 'alt_midpoints', 'alt_p_as'],
                                                  axis=1)
        elif not self.ui.gather_events_checkbox.isChecked():
            dropped_picks_df = self.picks_df.drop(['col_val', 'sta_id', 'time_mid'],
                                                  axis=1)

        # make string rep of time
        def mk_picks_UTC_str(row):
            return (pd.Series([UTCDateTime(row['P_pick_time_UTC']).ctime(),
                               UTCDateTime(row['P_as_pick_time_UTC']).ctime()]))

        dropped_picks_df[['P_time', 'P_as_time']] = dropped_picks_df.apply(mk_picks_UTC_str, axis=1)

        dropped_picks_df = dropped_picks_df.drop(['P_pick_time_UTC', 'P_as_pick_time_UTC'], axis=1)

        dropped_cat_df = self.asdf_cat_df

        # make UTC string from earthquake cat and add julian day column
        def mk_cat_UTC_str(row):
            return (pd.Series([UTCDateTime(row['qtime']).ctime(), UTCDateTime(row['qtime']).julday]))

        dropped_cat_df[['Q_time_str', 'julday']] = dropped_cat_df.apply(mk_cat_UTC_str, axis=1)

        self.picktbld = PickTableDialog(parent=self, cat_df=dropped_cat_df, pick_df=dropped_picks_df)

        # make pick table right clickable for plotting single station
        self.picktbld.pick_table_view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.picktbld.pick_table_view.customContextMenuRequested.connect(self.pick_tbl_view_popup)

        # make event table right clickable for sorting pick plot
        self.picktbld.cat_event_table_view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.picktbld.cat_event_table_view.customContextMenuRequested.connect(self.pick_tbl_view_popup)

        # Lookup Dictionary for table views
        self.tbl_view_dict = {"cat": self.picktbld.cat_event_table_view, "picks": self.picktbld.pick_table_view}

        # Create a new table_accefssor dictionary for this class
        # self.table_accessor = {self.picktbld.cat_event_table_view: [dropped_cat_df, range(0, len(dropped_cat_df))],
        #                        self.picktbld.pick_table_view: [dropped_picks_df, range(0, len(dropped_picks_df))]}

        self.table_accessor[self.picktbld.cat_event_table_view] = [dropped_cat_df, range(0, len(dropped_cat_df))]
        self.table_accessor[self.picktbld.pick_table_view] = [dropped_picks_df, range(0, len(dropped_picks_df))]

        self.picktbld.cat_event_table_view.clicked.connect(self.pick_table_view_clicked)
        self.picktbld.pick_table_view.clicked.connect(self.pick_table_view_clicked)

        # If headers are clicked then sort
        self.picktbld.cat_event_table_view.horizontalHeader().sectionClicked.connect(self.pick_headerClicked)
        self.picktbld.pick_table_view.horizontalHeader().sectionClicked.connect(self.pick_headerClicked)

    def scatter_point_deselect(self):
        try:
            for p in self.lastClicked:
                p.resetPen()
        except AttributeError:
            pass

    def scatter_point_select(self, scatter_index):
        # Select the point on the scatter plot
        selected_point_tbl = [self.time_diff_scatter_plot.points()[scatter_index]]
        for p in self.lastClicked:
            p.resetPen()
        for p in selected_point_tbl:
            p.setPen('r', width=2)
        self.lastClicked = selected_point_tbl

    def scatter_point_clicked(self, plot, points):
        if self.ui.gather_events_checkbox.isChecked():
            self.select_scatter_pick = self.picks_df.loc[(self.picks_df['alt_midpoints'] == points[0].pos()[0]) &
                                                         (self.picks_df['sta_id'] == points[0].pos()[1]), :]
        elif not self.ui.gather_events_checkbox.isChecked():
            self.select_scatter_pick = self.picks_df.loc[self.picks_df['time_mid'] == points[0].pos()[0]]
        pick_row_index = self.select_scatter_pick.index.tolist()[0]

        # if there is a catalogue loaded in attempt to highlight it on the list
        try:
            self.pick_table_view_highlight(self.picktbld.pick_table_view, pick_row_index)
        except AttributeError:
            print('Error: No Earthquake Catalogue is Loaded')
            pass

    def plot_single_stn_selected(self, plt_single_pushButton, stn):

        # get events just for the selected stn
        self.picks_df_stn = self.picks_df.loc[self.picks_df['sta'] == stn]

        # List of colors for individual scatter poinst based on the arr time residual
        col_list = self.picks_df_stn['col_val'].apply(lambda x: self.col_grad_w.getColor(x)).tolist()

        rearr_midpoint_dict = [(b, a) for a, b in self.midpoint_dict.iteritems()]

        x_axis_string = pg.AxisItem(orientation='bottom')
        x_axis_string.setTicks([rearr_midpoint_dict])

        # print(self.picks_df_stn)

        self.stnplt = SingleStnPlot(parent=self, picks_df_stn=self.picks_df_stn, col_list=col_list,
                                    x_axis_string=x_axis_string, stn=stn)

    def pick_tbl_view_popup(self):
        focus_widget = QtGui.QApplication.focusWidget()
        # get the selected row number
        row_number = focus_widget.selectionModel().selectedRows()[0].row()
        row_index = self.table_accessor[focus_widget][1][row_number]

        if focus_widget == self.picktbld.cat_event_table_view:
            selected_row = self.cat_df.loc[row_index]

            rc_menu = QtGui.QMenu(self)

            # set up the rec menu
            rc_menu.addAction('Sort by GCARC Dist', functools.partial(
                self.sort_method_selected, self.sort_drop_down_button, (selected_row['event_id'], 1), True))
            rc_menu.addAction('Sort by Azimuth', functools.partial(
                self.sort_method_selected, self.sort_drop_down_button, (selected_row['event_id'], 2), True))
            rc_menu.addAction('Sort by Ep Dist', functools.partial(
                self.sort_method_selected, self.sort_drop_down_button, (selected_row['event_id'], 3), True))

            rc_menu.addSeparator()

            # now add tools to display waveforms for given event
            rc_menu.addAction('Display Waveforms for Event', functools.partial(
                self.initialise_waveform_plot, (selected_row, "event")))

            rc_menu.popup(QtGui.QCursor.pos())

        elif focus_widget == self.picktbld.pick_table_view:

            selected_row = self.picks_df.loc[row_index]

            # set up right click menu
            rc_menu = QtGui.QMenu(self)
            rc_menu.addAction('Plot Single Station Residual', functools.partial(
                self.plot_single_stn_selected, self.plot_single_stn_button, selected_row['sta']))

            rc_menu.addSeparator()

            rc_menu.addAction('Display Waveform for Station', functools.partial(
                self.initialise_waveform_plot, (selected_row, "station")))

            rc_menu.popup(QtGui.QCursor.pos())

    def pick_table_view_highlight(self, focus_widget, row_index):

        if focus_widget == self.picktbld.cat_event_table_view:
            self.picktbld.pick_table_view.clearSelection()
            self.selected_row = self.asdf_cat_df.loc[row_index]

            # Find the row_number of this index
            cat_row_number = self.table_accessor[focus_widget][1].index(row_index)
            focus_widget.selectRow(cat_row_number)

            # Highlight the marker on the maps
            js_call = "highlightEvent('{event_id}');".format(event_id=self.selected_row['event_id'])
            self.ui.map_view_station.page().mainFrame().evaluateJavaScript(js_call)
            self.ui.map_view_events.page().mainFrame().evaluateJavaScript(js_call)

        elif focus_widget == self.picktbld.pick_table_view:
            self.selected_row = self.picks_df.loc[row_index]

            # Select the point on the scatter plot
            self.scatter_point_select(row_index)

            pick_row_number = self.table_accessor[focus_widget][1].index(row_index)
            focus_widget.selectRow(pick_row_number)

            # get the quake selected
            pick_tbl_event_id = self.selected_row['pick_event_id']
            cat_row_index = self.asdf_cat_df[self.asdf_cat_df['event_id'] == pick_tbl_event_id].index.tolist()[0]

            # Find the row_number of this index on the earthquake cat table
            cat_row_number = self.table_accessor[self.picktbld.cat_event_table_view][1].index(cat_row_index)
            self.picktbld.cat_event_table_view.selectRow(cat_row_number)

            # Highlight the event and station marker on the map
            js_call = "highlightEvent('{event_id}');".format(event_id=self.selected_row['pick_event_id'])
            self.ui.map_view_station.page().mainFrame().evaluateJavaScript(js_call)
            self.ui.map_view_events.page().mainFrame().evaluateJavaScript(js_call)

            js_call = "highlightStation('{station_id}');".format(station_id=self.selected_row['sta'])
            self.ui.map_view_station.page().mainFrame().evaluateJavaScript(js_call)
            self.ui.map_view_events.page().mainFrame().evaluateJavaScript(js_call)

    def pick_headerClicked(self, logicalIndex):
        focus_widget = QtGui.QApplication.focusWidget()
        table_df = self.table_accessor[focus_widget][0]

        header = focus_widget.horizontalHeader()

        self.order = header.sortIndicatorOrder()
        table_df.sort_values(by=table_df.columns[logicalIndex],
                             ascending=self.order, inplace=True)

        self.table_accessor[focus_widget][1] = table_df.index.tolist()

        if focus_widget == self.picktbld.cat_event_table_view:
            self.model = PandasModel(table_df, cat_nm=True)
        elif focus_widget == self.picktbld.pick_table_view:
            self.model = PandasModel(table_df, pick_nm=True)

        focus_widget.setModel(self.model)
        focus_widget.update()

    def pick_table_view_clicked(self):
        focus_widget = QtGui.QApplication.focusWidget()
        row_number = focus_widget.selectionModel().selectedRows()[0].row()
        row_index = self.table_accessor[focus_widget][1][row_number]
        # Highlight/Select the current row in the table
        self.pick_table_view_highlight(focus_widget, row_index)

    def events_to_df(self):
        """
        get events from ASDF events and convert into dataframe
        then display dataframe as table
        """
        # create empty data frame
        self.asdf_cat_df = pd.DataFrame(data=None, columns=['event_id', 'qtime', 'lat', 'lon', 'depth', 'mag'])

        # iterate through the events
        for _i, event in enumerate(self.events):
            # Get quake origin info
            origin_info = event.preferred_origin() or event.origins[0]

            # check if the eventID is in the pick files.
            if not str(event.resource_id.id).split('=')[1] in self.picks_df['pick_event_id'].values:
                continue

            try:
                mag_info = event.preferred_magnitude() or event.magnitudes[0]
                magnitude = mag_info.mag
            except IndexError:
                # No magnitude for event
                magnitude = None

            self.asdf_cat_df.loc[_i] = [str(event.resource_id.id).split('=')[1], int(origin_info.time.timestamp),
                                        origin_info.latitude, origin_info.longitude,
                                        origin_info.depth / 1000, magnitude]

        self.asdf_cat_df.reset_index(drop=True, inplace=True)

        # now build the eq cat and pick table
        print('------------')
        print(self.asdf_cat_df)
        self.build_pick_tables()
        self.p_time_plot_events()
        self.p_time_plot_stations()

        midpoint_list = []
        # sort the events by time:
        sorted_events_list = self.asdf_cat_df.sort_values(by='qtime')['event_id'].tolist()

        # Now work out x axis values such that the first (in time) event is 0
        for _i, event in enumerate(sorted_events_list):
            # print(event)
            if _i == 0:
                midpoint_list.append(0)
                print
                '...'
                continue

            select_prev_events_df = self.picks_df.loc[self.picks_df['pick_event_id'] == sorted_events_list[_i - 1]]
            select_events_df = self.picks_df.loc[self.picks_df['pick_event_id'] == event]

            max_prev_tt_diff = (select_prev_events_df['tt_diff'].abs() / 2).max()
            max_current_tt_diff = (select_events_df['tt_diff'].abs() / 2).max()

            max_right = midpoint_list[_i - 1] + max_prev_tt_diff

            midpoint = max_right + 0.5 + max_current_tt_diff

            midpoint_list.append(midpoint)

        self.midpoint_dict = dict(zip(sorted_events_list, midpoint_list))

        # assign the new midpoint values to the picks dataframe
        def add_x_midpoints(row):
            alt_midpoint = self.midpoint_dict[row['pick_event_id']]

            alt_p_as = alt_midpoint - row['tt_diff'] / 2

            return (pd.Series([alt_midpoint, alt_p_as]))

        self.picks_df[['alt_midpoints', 'alt_p_as']] = self.picks_df.apply(add_x_midpoints, axis=1)

        print('-------------')
        print(self.picks_df)

    def p_time_plot_events(self):
        # Plot the events
        for row_index, row in self.asdf_cat_df.iterrows():
            js_call = "addEvent('{event_id}', '{df_id}', {row_index}, " \
                      "{latitude}, {longitude}, '{a_color}', '{p_color}');" \
                .format(event_id=row['event_id'], df_id="cat", row_index=int(row_index), latitude=row['lat'],
                        longitude=row['lon'], a_color="Red",
                        p_color="#008000")
            self.ui.map_view_station.page().mainFrame().evaluateJavaScript(js_call)
            self.ui.map_view_events.page().mainFrame().evaluateJavaScript(js_call)

    def p_time_plot_stations(self):

        # make colors for different networks in ASDF file
        print(self.ASDF_accessor[self.ds_id]['net_list'])
        color_map = ["#3D8EC9", "Yellow", "Green"]

        col_list = [color_map[x] for x in range(len(self.ASDF_accessor[self.ds_id]['net_list']))]
        col_dict = dict(zip(self.ASDF_accessor[self.ds_id]['net_list'], col_list))

        print(col_dict)

        print(self.ASDF_accessor[self.ds_id]['net_sta_list'])
        net_sta_list = self.ASDF_accessor[self.ds_id]['net_sta_list']

        # iterate through and get the inventory
        for net_sta in net_sta_list:
            inv = self.ds.waveforms[net_sta].StationXML
            station_inv = inv[0][0]

            # generate net_sta unique list in picks df
            unique_picks_net_sta_set = set()
            for nscl in self.picks_df["sta"]:
                unique_picks_net_sta_set.add(nscl.split('_')[0] + "_" + nscl.split('_')[1])

            unique_picks_net_sta_list = list(unique_picks_net_sta_set)

            if inv[0].code + "_" + station_inv.code in unique_picks_net_sta_list:
                col_dict[inv[0].code]
                # create the input html string for the passive icon
                in_html = '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" style="margin: 0 auto; width: 20px; ' \
                          'height:20px;"><polygon style="fill:' + col_dict[
                              inv[0].code] + '; stroke:#666666; stroke-width:2; ' \
                                             'stroke-opacity:0.5"points="0,0 20,0 10,20"/></svg>'

                print(station_inv.code)
                js_call = "addStation('{station_id}', {latitude}, {longitude}, '{in_html}');" \
                    .format(station_id=inv[0].code + "_" + station_inv.code, latitude=station_inv.latitude,
                            longitude=station_inv.longitude, in_html=in_html)

                print(js_call)
                self.ui.map_view_station.page().mainFrame().evaluateJavaScript(js_call)
                self.ui.map_view_events.page().mainFrame().evaluateJavaScript(js_call)

    def build_event_tree_view(self):
        if not hasattr(self, "ds") or not self.ds:
            return
        self.events = self.ds.events
        self.ui.event_tree_widget.clear()

        items = []
        self._state["quake_ids"] = {}

        if not len(self.events) == 0:

            # create empty data frame
            self.cat_df = pd.DataFrame(data=None, columns=['event_id', 'qtime', 'lat', 'lon', 'depth', 'mag'])

            # iterate through the events
            for _i, event in enumerate(self.events):
                if event.origins:
                    org = event.preferred_origin() or event.origins[0]

                    js_call = "addEvent('{event_id}', {latitude}, {longitude});" \
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

                # Get quake origin info
                origin_info = event.preferred_origin() or event.origins[0]

                try:
                    mag_info = event.preferred_magnitude() or event.magnitudes[0]
                    magnitude = mag_info.mag
                except IndexError:
                    # No magnitude for event
                    magnitude = None

                self.cat_df.loc[_i] = [str(event.resource_id.id).split('=')[1], int(origin_info.time.timestamp),
                                       origin_info.latitude, origin_info.longitude,
                                       origin_info.depth / 1000, magnitude]

            self.cat_df.reset_index(drop=True, inplace=True)

            print('------------')
            print(self.cat_df)

            self.build_EQ_extract_table()

            self.ui.event_tree_widget.insertTopLevelItems(0, items)

    def build_station_view_list(self):
        if not hasattr(self, "ds") or not self.ds:
            return

        print("Building Station View List.....")

        items = []

        # persistent list for all stations within ASDF file
        net_sta_list = []
        # set for all networks in ASDF file
        net_set = set()

        filename_item = QtGui.QTreeWidgetItem([self.ds_id],
                                              type=STATION_VIEW_ITEM_TYPES["FILE"])

        # add the tree item for the ASDF file into accessor dict as well as the default background
        self.ASDF_accessor[self.ds_id]['file_tree_item'] = filename_item
        # self.ASDF_accessor[self.ds_id]['def_bkgrnd_col'] = filename_item.background(0)

        print("Selecting network...")
        self.on_station_view_itemClicked(filename_item)

        # Iterate through station accessors in ASDF file just extract to station level for now
        for key, group in itertools.groupby(
                self.ds.waveforms,
                key=lambda x: x._station_name.split(".")[0]):
            network_item = QtGui.QTreeWidgetItem(
                [key],
                type=STATION_VIEW_ITEM_TYPES["NETWORK"])
            group = sorted(group, key=lambda x: x._station_name)
            # Add all children stations.
            for station in sorted(group, key=lambda x: x._station_name):
                station_item = QtGui.QTreeWidgetItem([
                    station._station_name.split(".")[-1]],
                    type=STATION_VIEW_ITEM_TYPES["STATION"])

                net_sta_list.append(station._station_name)
                net_set.add(station._station_name.split('.')[0])

                network_item.addChild(station_item)
            filename_item.addChild(network_item)
        items.append(filename_item)

        self.ui.station_view.insertTopLevelItems(0, items)

        # get the unique channel codes and tags in ASDF file
        unq_chan, unq_tags = self.ASDF_accessor[self.ds_id]["db"].get_unique_information()

        print(unq_chan, unq_tags)

        # make the channel code set into list and make persistant
        self.ASDF_accessor[self.ds_id]['net_list'] = list(net_set)
        self.ASDF_accessor[self.ds_id]['channel_codes'] = unq_chan
        self.ASDF_accessor[self.ds_id]['net_sta_list'] = net_sta_list
        self.ASDF_accessor[self.ds_id]['tags_list'] = unq_tags
        print("done Building station view")

    def build_auxillary_tree_view(self):
        self.ui.auxiliary_data_tree_view.clear()

        # Also add the auxiliary data.
        # Note: it seems slow to read in all of the child information for Auxillary data
        # for now only read in child info when auxillary parent item is clicked

        items = []
        for data_type in self.ds.auxiliary_data.list():
            data_type_item = QtGui.QTreeWidgetItem(
                [data_type],
                type=AUX_DATA_ITEM_TYPES["DATA_TYPE"])

            # get children one level down
            children = []
            for sub_item in self.ds.auxiliary_data[data_type].list():
                child_item = QtGui.QTreeWidgetItem(
                    [sub_item],
                    type=AUX_DATA_ITEM_TYPES["DATA_TYPE"])
                children.append(child_item)
            data_type_item.addChildren(children)

            items.append(data_type_item)
        self.ui.auxiliary_data_tree_view.insertTopLevelItems(0, items)
        print("Done Building Aux")

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

    def read_ASDF_info(self):
        print("Reading ASDF Info....")

        # save all of the coords for later
        temp_x_coords = []
        temp_y_coords = []

        for station_id, coordinates in self.ds.get_all_coordinates().items():
            if not coordinates:
                continue

            latitude = coordinates["latitude"]
            longitude = coordinates["longitude"]

            # append the lats and lons to temp lists
            temp_x_coords.append(longitude)
            temp_y_coords.append(latitude)

            js_call = "addStation('{station_id}', {latitude}, {longitude})"
            self.ui.web_view.page().mainFrame().evaluateJavaScript(
                js_call.format(station_id=station_id,
                               latitude=latitude,
                               longitude=longitude))

        self.ASDF_accessor[self.ds_id]['station_coords'] = (temp_x_coords, temp_y_coords)

        print("Building Event Tree View.....")
        self.build_event_tree_view()

        # Add all the provenance items
        self.provenance_list_model.clear()
        for provenance in self.ds.provenance.list():
            item = QtGui.QStandardItem(provenance)
            self.provenance_list_model.appendRow(item)

        print("Building Auxillary Tree View.....")
        self.build_auxillary_tree_view()

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
        print(" done with ASDF info...")

    def open_json_file(self, asdf_file):
        # automatically get associated JSON database file if it exists
        db_file = glob.glob(join(dirname(asdf_file), '*.json'))

        if not len(db_file) == 0:

            print('')
            print("Initializing Database..")

            # create the seismic database
            seisdb = SeisDB(json_file=db_file[0])

            # add it to the asdf accessor
            self.ASDF_accessor[os.path.basename(asdf_file)]["db"] = seisdb

            print("Seismic Database Initilized!")

        else:
            # create a JSON database for the ASDF file
            # TODO: write JSON db build
            pass

    def open_asdf_file(self):
        """
        Fill the station tree widget upon opening a new file.
        """
        asdf_file = str(QtGui.QFileDialog.getOpenFileName(
            parent=self, caption="Choose ASDF File",
            directory=os.path.expanduser("~"),
            filter="ASDF files (*.h5)"))
        if not asdf_file:
            return

        # asdf_file = "/g/data1/ha3/XX_EQ_test.h5"

        asdf_filename = basename(asdf_file)

        ds = pyasdf.ASDFDataSet(asdf_file)

        # add the asdf filename as key and the dataset into the file accessor
        self.ASDF_accessor[asdf_filename] = {"ds": ds}

        # open the associated JSON database if it exists
        self.open_json_file(asdf_file)

        # # call the function to get the currently selected db and ds
        # self.change_active_ASDF(asdf_filename)

        self.ds = self.ASDF_accessor[asdf_filename]["ds"]
        self.db = self.ASDF_accessor[asdf_filename]["db"]
        self.ds_id = asdf_filename

        self.build_station_view_list()

        # self.read_ASDF_info()

    def open_EQ_cat(self):
        self.cat_filename = str(QtGui.QFileDialog.getOpenFileName(
            parent=self, caption="Choose Earthquake QuakeML File",
            directory=os.path.expanduser("~"),
            filter="QuakeML files (*.xml)"))
        if not self.cat_filename:
            return

        self.cat = read_events(self.cat_filename)

        # create empty data frame
        self.cat_df = pd.DataFrame(data=None, columns=['event_id', 'qtime', 'lat', 'lon', 'depth', 'mag'])

        # iterate through the events
        for _i, event in enumerate(self.cat):
            # Get quake origin info
            origin_info = event.preferred_origin() or event.origins[0]

            try:
                mag_info = event.preferred_magnitude() or event.magnitudes[0]
                magnitude = mag_info.mag
            except IndexError:
                # No magnitude for event
                magnitude = None

            self.cat_df.loc[_i] = [str(event.resource_id.id).split('=')[1], int(origin_info.time.timestamp),
                                   origin_info.latitude, origin_info.longitude,
                                   origin_info.depth / 1000, magnitude]

        self.cat_df.reset_index(drop=True, inplace=True)

        print('------------')
        print(self.cat_df)

        self.build_EQ_extract_table()


        # TODO: add extract earthquake functionality similar to QC_events_ASDF GUI

        # add into new ASDF file
        # open the catalogue in a dataframe view under events tab

    def EQ_tbl_view_popup(self):

        focus_widget = QtGui.QApplication.focusWidget()
        # get the selected row number
        row_number = focus_widget.selectionModel().selectedRows()[0].row()
        self.cat_row_index = self.table_accessor[focus_widget][1][row_number]

        self.selected_row = self.cat_df.loc[self.cat_row_index]

        net_sta_list = self.ASDF_accessor[self.ds_id]['net_sta_list']
        print(net_sta_list)

        # get a list of unique networks and stations
        net_list = list(set([x.split('.')[0] for x in net_sta_list]))
        sta_list = [x.split('.')[1] for x in net_sta_list]

        chan_list = self.ASDF_accessor[self.ds_id]['channel_codes']
        tags_list = self.ASDF_accessor[self.ds_id]['tags_list']

        xtract_start = UTCDateTime(self.selected_row['qtime'])
        xtract_end = UTCDateTime(self.selected_row['qtime'])

        self.rc_menu = QtGui.QMenu(self)
        self.rc_menu.addAction('Extract Earthquake', functools.partial(
            self.extract_waveform_frm_ASDF, False,
            net_list=net_list,
            sta_list=sta_list,
            chan_list=chan_list,
            tags_list=tags_list,
            ph_st=str(xtract_start).split('.')[0],
            ph_et=str(xtract_end).split('.')[0],
            xquake=True))
        # event_id=self.selected_row['event_id'],
        # event_df=self.selected_row))

        self.rc_menu.popup(QtGui.QCursor.pos())

    def trace_tbl_view_popup(self):
        focus_widget = QtGui.QApplication.focusWidget()

        # get the selected row number
        row_number = focus_widget.selectionModel().selectedRows()[0].row()

        self.selected_row = self.trace_df.loc[row_number]

        trace_id = self.selected_row["ASDF_id"]

        rc_menu = QtGui.QMenu(self)
        rc_menu.addAction("Plot Trace", functools.partial(self.trace_selected, trace_id))

        rc_menu.popup(QtGui.QCursor.pos())

    def trace_selected(self, trace_id):
        self.st = self.sta_accessor[trace_id]

        print(self.st)
        self.update_waveform_plot()

    def build_EQ_extract_table(self):

        # self.table_accessor = None

        dropped_cat_df = self.cat_df

        # make UTC string from earthquake cat and add julian day column
        def mk_cat_UTC_str(row):
            return (pd.Series([UTCDateTime(row['qtime']).ctime(), UTCDateTime(row['qtime']).julday]))

        dropped_cat_df[['Q_time_str', 'julday']] = dropped_cat_df.apply(mk_cat_UTC_str, axis=1)

        # earthquake table dialog
        self.EQtbld = EqTableDialog(parent=self, cat_df=dropped_cat_df)

        self.EQtbld.tbldui.EQ_xtract_tableView.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.EQtbld.tbldui.EQ_xtract_tableView.customContextMenuRequested.connect(self.EQ_tbl_view_popup)

        # extract all or selected earthquakes
        self.EQtbld.tbldui.xtract_selected_pushButton.released.connect(
            functools.partial(self.extract_multi_quakes, "selected"))
        self.EQtbld.tbldui.xtract_all_pushButton.released.connect(functools.partial(self.extract_multi_quakes, "all"))

        # Lookup Dictionary for table views
        self.tbl_view_dict = {"cat": self.EQtbld.tbldui.EQ_xtract_tableView}

        # Create a new table_accessor dictionary for this class
        # self.table_accessor = {self.EQtbld.tbldui.EQ_xtract_tableView: [dropped_cat_df, range(0, len(dropped_cat_df))]}
        self.table_accessor[self.EQtbld.tbldui.EQ_xtract_tableView] = [dropped_cat_df, range(0, len(dropped_cat_df))]

        self.EQtbld.tbldui.EQ_xtract_tableView.clicked.connect(self.EQ_table_view_clicked)

        # If headers are clicked then sort
        self.EQtbld.tbldui.EQ_xtract_tableView.horizontalHeader().sectionClicked.connect(self.EQ_headerClicked)

    def EQ_headerClicked(self, logicalIndex):
        focus_widget = QtGui.QApplication.focusWidget()
        table_df = self.table_accessor[focus_widget][0]

        header = focus_widget.horizontalHeader()

        self.order = header.sortIndicatorOrder()
        table_df.sort_values(by=table_df.columns[logicalIndex],
                             ascending=self.order, inplace=True)

        self.table_accessor[focus_widget][1] = table_df.index.tolist()

        if focus_widget == self.EQtbld.tbldui.EQ_xtract_tableView:
            self.model = PandasModel(table_df, cat_nm=True)

        focus_widget.setModel(self.model)
        focus_widget.update()

    def EQ_table_view_clicked(self):
        focus_widget = QtGui.QApplication.focusWidget()
        row_number = focus_widget.selectionModel().selectedRows()[0].row()
        row_index = self.table_accessor[focus_widget][1][row_number]
        # Highlight/Select the current row in the table
        self.EQ_table_view_highlight(focus_widget, row_index)

    def EQ_table_view_highlight(self, focus_widget, row_index):

        cat_df = self.table_accessor[focus_widget][0]

        if focus_widget == self.EQtbld.tbldui.EQ_xtract_tableView:
            self.selected_row = cat_df.loc[row_index]

            # print(self.selected_row)

            # Find the row_number of this index
            cat_row_number = self.table_accessor[focus_widget][1].index(row_index)
            focus_widget.selectRow(cat_row_number)

            # Highlight the marker on the map
            js_call = "highlightEvent('{event_id}');".format(event_id=self.selected_row['event_id'])
            self.EQtbld.tbldui.EQ_xtract_webView.page().mainFrame().evaluateJavaScript(js_call)

    def on_detrend_and_demean_check_box_stateChanged(self, state):
        self.update_waveform_plot()

    def on_normalize_check_box_stateChanged(self, state):
        self.update_waveform_plot()

    def on_group_by_network_check_box_stateChanged(self, state):
        self.build_station_view_list()

    def waveform_filter_settings(self):
        # open the filter dialog window to set filter settings
        fil_dlg = FilterDialog(parent=self)
        if fil_dlg.exec_():
            self.filter_settings = fil_dlg.get_arguments()

            print(self.filter_settings)

            # if the filter box is checked then once the filter settings are set update the waveform plot with filter applied
            if self.ui.waveform_filter_check_box.isChecked():
                self.update_waveform_plot()

    def on_waveform_filter_check_box_stateChanged(self, state):
        self.update_waveform_plot()

    def gather_events_checkbox_changed(self):
        self.sort_method_selected(self.ui.sort_drop_down_button_2, ('no_sort', 'no_sort'), False)

    def sort_method_selected(self, sort_pushButton, value, prev_view):
        """
        # Method to plot information on the scatter plot and to provide sort functionality
        # All calls to update the waveform plot area should pass through here rather than calling update_waveform_plot
        """

        # Method to plot information on the scatter plot and to provide sort functionality
        # All calls to update the plot area should pass through here rather than calling update_plot
        if prev_view:
            try:
                self.saved_state = self.plot.getViewBox().getState()
            except AttributeError:
                # Plot does not exist, i.e. it is the first time trying to call update_graph
                self.saved_state = None
        elif not prev_view:
            self.saved_state = None
        # if no sort:
        if value[1] == "no_sort":
            sort_meth = None
            sort_pushButton.setText("Sort")
            unique_stations = self.picks_df['sta'].unique()

            stn_list = unique_stations.tolist()
            stn_list.sort()

            self.axis_station_list = stn_list
        # if sort by station:
        elif value[1] == 0:
            sort_pushButton.setText(value[0])
            sort_meth = "station"
            self.axis_station_list = np.sort(self.picks_df['sta'].unique())  # numpy array
        # if sort by gcarc
        elif value[1] == 1:
            sort_pushButton.setText("Sorted by GCARC: " + value[0])
            sort_meth = 'gcarc'
            self.axis_station_list = self.spatial_dict[value[0]].sort_values(by='gcarc')['station'].tolist()
        # if sort by azimuth
        elif value[1] == 2:
            sort_pushButton.setText("Sorted by AZ: " + value[0])
            sort_meth = 'az'
            self.axis_station_list = self.spatial_dict[value[0]].sort_values(by='az')['station'].tolist()
        # if sort by ep dist
        elif value[1] == 3:
            sort_pushButton.setText("Sorted by Ep Dist: " + value[0])
            sort_meth = 'ep_dist'
            self.axis_station_list = self.spatial_dict[value[0]].sort_values(by='ep_dist')['station'].tolist()

        # use sort method unless it is None (i.e. No sort)
        if sort_meth:
            self.axis_station_list = self.spatial_dict[value[0]].sort_values(by=sort_meth)['station'].tolist()

            # sort the waveform if it exists
            try:
                self.st.sort(keys=[sort_meth])
            except AttributeError:
                # stream does not exist
                pass

        # self.update_waveform_plot()
        self.update_p_time_graph()

    def on_reset_view_push_button_released(self):
        """
        Method to reset the waveform plot to initial
        """
        self.sort_method_selected(self.ui.sort_drop_down_button_2, ('no_sort', 'no_sort'), False)

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

    def keyPressEvent(self, event):
        """
        Overide the key press event, so that it can catch when P or S is used as a modifier during mouseclick
        :param event:
        :return:
        """

        if event.isAutoRepeat():
            self.modifier_key = ""
            return

        event = event.key()

        if (event == QtCore.Qt.Key_P):
            self.modifier_key = "P_as"
        elif (event == QtCore.Qt.Key_S):
            self.modifier_key = "S_as"
        else:
            self.modifier_key = ""

    def waveform_widget_itemClicked(self, event):

        print("dealing with mouse click")
        print("Active Plot:", self._state["station_id"][self.active_tr_index])

        arr_name = None

        # items = self.waveform_graph.scene().items(event.scenePos())
        # sel_plot = [x for x in items if isinstance(x, pg.PlotItem)][0]
        # print(sel_plot)
        # print(self.active_plot)
        # pos = QtCore.QPointF(event.scenePos())
        # vb = sel_plot.vb
        #
        # if sel_plot.sceneBoundingRect().contains(pos):
        #     mousePoint = vb.mapSceneToView(pos)
        #     temp_timestamp = mousePoint.x()
        # else:
        #     return

        # check if there are any modifiers
        shift_query = event.modifiers() == QtCore.Qt.ShiftModifier
        scroll_btn_query = event.button() == 4
        mod_key_query = len(self.modifier_key) > 0

        if shift_query or scroll_btn_query or mod_key_query:
            # get timestamp for the pick
            pos = QtCore.QPointF(event.scenePos())
            vb = self.active_plot.vb
            mousePoint = vb.mapSceneToView(pos)
            temp_timestamp = mousePoint.x()

            # pos = QtCore.QPointF(event.scenePos())
            # vb = self.active_plot.vb
            # if self.active_plot.sceneBoundingRect().contains(pos):
            #     print("On active plot")
            #     mousePoint = vb.mapSceneToView(pos)
            #     temp_timestamp = mousePoint.x()
            # else:
            #     print("not on active plot")

            if (shift_query and not mod_key_query) or scroll_btn_query:
                # pop up text box for the name of the arrival
                arr_name, ok = QtGui.QInputDialog.getText(self, "Arrival Picker Input", "Arrival Name",
                                                                   QtGui.QLineEdit.Normal, "TestArrival")
                if ok:
                    arr_name = str(arr_name)
                    self.ASDF_arrival_picker(arr_name, temp_timestamp)

            elif mod_key_query:
                arr_name = self.modifier_key
                self.ASDF_arrival_picker(arr_name, temp_timestamp)

    def ASDF_arrival_picker(self, arr_name, arr_timestamp):
        """
        Method to pick and save arrivals (or anything else) referneced to a specific time/station/channel etc...
        :return:
        """

        print("opening arrival picker")
        # get the currently selected earthquake in the earthquake table
        row_number = self.EQtbld.tbldui.EQ_xtract_tableView.selectionModel().selectedRows()[0].row()
        row_index = self.table_accessor[self.EQtbld.tbldui.EQ_xtract_tableView][1][row_number]

        # get the event id
        self.select_quake = self.cat_df.loc[row_index]
        # print(self.select_quake)
        print(self.select_quake["event_id"])

        tr_id = self._state["station_id"][self.active_tr_index]

        # tempoaraly save the auxillary data
        temp_arrival_aux = self.ds.auxiliary_data.ArrivalData[self.select_quake["event_id"]][
            tr_id.replace(".", "_")]
        # print(temp_arrival_aux)

        # update the parameters with the pick


        temp_arrival_aux.parameters[arr_name] = str(UTCDateTime(arr_timestamp))

        # delete the old auxillary data from ASDF
        del self.ds.auxiliary_data.ArrivalData[self.select_quake["event_id"]][
            tr_id.replace(".", "_")]

        data_type = "ArrivalData"
        data_path = self.select_quake["event_id"] + "/" + tr_id.replace(".", "_")

        # now add updated aux data back in
        self.ds.add_auxiliary_data(data=temp_arrival_aux.data,
                                   data_type=data_type,
                                   path=data_path,
                                   parameters=temp_arrival_aux.parameters)

        # print('.....')
        # print(temp_arrival_aux)
        print('.....')
        print(self.ds.auxiliary_data.ArrivalData[self.select_quake["event_id"]][
                  tr_id.replace(".", "_")])

        self.st[self.active_tr_index].stats[arr_name.lower()] = arr_timestamp

        print(self.st[self.active_tr_index])

        # print(self.st[self.active_tr_index].stats)
        self.waveform_plot_interact()

        # set the modification tracker to True
        self.arrivals_modified = True

        # self.ASDF_accessor[asdf_filename] = {"ds": ds}

        # re run the auxillary data view
        self.build_auxillary_tree_view()

    def waveform_plot_mouseMoved(self, pos):
        # print("Mouse Moved in Waveform Widget")

        # find the plot the mouse is over
        for _i, plot in enumerate(self._state["waveform_plots"]):
            vb = plot.vb
            if plot.sceneBoundingRect().contains(pos):
                self.active_plot = plot
                self.active_tr_index = _i

                # set tooltip with the data coordinates
                self.dispMousePos(pos)

    def waveform_plot_interact(self):
        # Method to catch when waveform plot is interacted with and will return the
        # interacted with plot (i.e. station) and will also resize the Arrival time vertical lines

        temp_st = self.st.copy()

        # self.active_plot = plot_args[0]
        # self.active_tr_index = plot_args[1]

        # print("Plot interacted with: ", self.active_plot)

        # # when mouse is clicked with modifer then open picker
        # self.active_plot.scene().sigMouseClicked.connect(self.on_graph_itemClicked)

        p_line = self._state["p_lines"][self.active_tr_index]
        p_as_line = self._state["p_as_lines"][self.active_tr_index]
        p_text = self._state["p_text"][self.active_tr_index]
        p_as_text = self._state["p_as_text"][self.active_tr_index]

        if not (p_line == None or p_as_line == None):
            # get the corresponding trace
            tr = temp_st[self.active_tr_index]

            axY = self.active_plot.getAxis('left')

            p_as_line.setData(np.array([tr.stats.p_as, tr.stats.p_as]),
                              np.array([axY.range[0] + 0.05 * (axY.range[1] - axY.range[0]),
                                        axY.range[1] - 0.05 * (axY.range[1] - axY.range[0])]),
                              pen=pg.mkPen({'color': '#ff8000', 'width': 1}))
            p_line.setData(np.array([tr.stats.p, tr.stats.p]),
                           np.array([axY.range[0] + 0.05 * (axY.range[1] - axY.range[0]),
                                     axY.range[1] - 0.05 * (axY.range[1] - axY.range[0])]),
                           pen=pg.mkPen({'color': '#40ff00', 'width': 1}))

            p_as_text.setPos(tr.stats.p_as, axY.range[0] + 0.1 * (axY.range[1] - axY.range[0]))
            p_text.setPos(tr.stats.p, axY.range[0] + 0.1 * (axY.range[1] - axY.range[0]))

    def dispMousePos(self, pos):
        # Display current mouse coords if over the scatter plot area as a tooltip

        x_coord = UTCDateTime(self.active_plot.vb.mapSceneToView(pos).toPoint().x())
        time_tool = self.active_plot.setToolTip(x_coord.ctime())

    def update_p_time_graph(self):
        # List of colors for individual scatter points based on the arr time residual
        col_list = self.picks_df['col_val'].apply(lambda x: self.ui.col_grad_w.getColor(x)).tolist()

        self.ui.p_graph_view.clear()

        # generate unique stationID integers
        # unique_stations = self.picks_df['sta'].unique()
        enum_sta = list(enumerate(self.axis_station_list))

        # rearrange dict
        sta_id_dict = dict([(b, a) for a, b in enum_sta])

        def get_sta_id(x):
            return (sta_id_dict[x['sta']])

        # add column with sta_id to picks df
        self.picks_df['sta_id'] = self.picks_df.apply(get_sta_id, axis=1)

        y_axis_string = pg.AxisItem(orientation='left')
        y_axis_string.setTicks([enum_sta])

        if not self.ui.gather_events_checkbox.isChecked():

            # Set up the plotting area
            self.plot = self.ui.p_graph_view.addPlot(0, 0, title="Time Difference: P Theoretical - P Picked (crosses)",
                                                     axisItems={'bottom': DateAxisItem(orientation='bottom',
                                                                                       utcOffset=0),
                                                                'left': y_axis_string})
            self.plot.setMouseEnabled(x=True, y=False)

            # # When Mouse is moved over plot print the data coordinates
            # self.plot.scene().sigMouseMoved.connect(self.dispMousePos)

            # Re-establish previous map_view_station if it exists
            if self.saved_state:
                self.plot.getViewBox().setState(self.saved_state)

            # Plot Error bar showing P and Pas arrivals
            diff_frm_mid = (self.picks_df['tt_diff'].abs() / 2).tolist()
            err = pg.ErrorBarItem(x=self.picks_df['time_mid'], y=self.picks_df['sta_id'], left=diff_frm_mid,
                                  right=diff_frm_mid, beam=0.05)

            self.plot.addItem(err)

            # Plot extra scatter points showing P_as picked (crosses)
            p_as_scatter_plot = pg.ScatterPlotItem(pxMode=True)
            p_as_scatter_plot.addPoints(self.picks_df['P_as_pick_time_UTC'],
                                        self.picks_df['sta_id'], symbol="+", size=10,
                                        brush=col_list)
            self.plot.addItem(p_as_scatter_plot)

            # Plot midway scatter points between time diff
            self.time_diff_scatter_plot = pg.ScatterPlotItem(pxMode=True)
            self.lastClicked = []
            self.time_diff_scatter_plot.sigClicked.connect(self.scatter_point_clicked)
            self.time_diff_scatter_plot.addPoints(self.picks_df['time_mid'],
                                                  self.picks_df['sta_id'], size=9,
                                                  brush=col_list)
            self.plot.addItem(self.time_diff_scatter_plot)

        elif self.ui.gather_events_checkbox.isChecked():

            rearr_midpoint_dict = [(b, a) for a, b in self.midpoint_dict.iteritems()]

            x_axis_string = pg.AxisItem(orientation='bottom')
            x_axis_string.setTicks([rearr_midpoint_dict])

            # Set up the plotting area
            self.plot = self.ui.p_graph_view.addPlot(0, 0, title="Time Difference: P Theoretical - P Picked (crosses)",
                                                     axisItems={'left': y_axis_string, 'bottom': x_axis_string})
            self.plot.setMouseEnabled(x=True, y=False)
            self.plot.setLabel('bottom', "Event ID")

            # Re-establish previous view if it exists
            if self.saved_state:
                self.plot.getViewBox().setState(self.saved_state)

            # Plot Error bar showing P and Pas arrivals
            diff_frm_mid = (self.picks_df['tt_diff'].abs() / 2).tolist()
            err = pg.ErrorBarItem(x=self.picks_df['alt_midpoints'], y=self.picks_df['sta_id'], left=diff_frm_mid,
                                  right=diff_frm_mid, beam=0.05)

            self.plot.addItem(err)

            # Plot extra scatter points showing P picked (crosses)
            p_as_scatter_plot = pg.ScatterPlotItem(pxMode=True)
            p_as_scatter_plot.addPoints(self.picks_df['alt_p_as'],
                                        self.picks_df['sta_id'], symbol="+", size=10,
                                        brush=col_list)
            self.plot.addItem(p_as_scatter_plot)

            # Plot midway scatter points between time diff
            self.time_diff_scatter_plot = pg.ScatterPlotItem(pxMode=True)
            self.lastClicked = []
            self.time_diff_scatter_plot.sigClicked.connect(self.scatter_point_clicked)
            self.time_diff_scatter_plot.addPoints(self.picks_df['alt_midpoints'],
                                                  self.picks_df['sta_id'], size=9,
                                                  brush=col_list)
            self.plot.addItem(self.time_diff_scatter_plot)

    def update_waveform_plot(self):
        # TODO: add ability to decouple an axis
        # add picking functionality
        self.ui.central_tab.setCurrentIndex(0)
        self.ui.reset_view_push_button.setEnabled(True)
        self.ui.previous_interval_push_button.setEnabled(True)
        self.ui.next_interval_push_button.setEnabled(True)
        self.ui.references_push_button.setEnabled(True)
        self.ui.normalize_check_box.setEnabled(True)
        self.ui.detrend_and_demean_check_box.setEnabled(True)
        self.ui.waveform_filter_check_box.setEnabled(True)
        self.ui.waveform_filter_settings_toolButton.setEnabled(True)
        # self.ui.pick_arrivals_tool_button.setEnabled(True)

        # Get the filter settings.
        filter_settings = {}
        filter_settings["detrend_and_demean"] = \
            self.ui.detrend_and_demean_check_box.isChecked()
        filter_settings["normalize"] = self.ui.normalize_check_box.isChecked()
        filter_settings["wavefilter"] = self.ui.waveform_filter_check_box.isChecked()

        temp_st = self.st.copy()

        if filter_settings["detrend_and_demean"]:
            temp_st.detrend("linear")
            temp_st.detrend("demean")

        if filter_settings["normalize"]:
            temp_st.normalize()

        if filter_settings["wavefilter"]:
            if hasattr(self, "filter_settings"):
                print(self.filter_settings)

                # # remove the df from the args dict
                # self.filter_args[1].pop("df", 0)

                print(self.filter_settings)

                temp_st.filter(self.filter_settings[0], **self.filter_settings[1])
            else:
                temp_st.filter("bandpass", freqmin=0.01, freqmax=10)

        self.waveform_graph.clear()
        self.waveform_graph.setMinimumPlotHeight(200)

        starttimes = []
        endtimes = []
        min_values = []
        max_values = []

        self._state["waveform_plots"] = []
        self._state["station_id"] = []
        self._state["station_tag"] = []
        self._state["p_lines"] = []
        self._state["p_as_lines"] = []
        self._state["p_text"] = []
        self._state["p_as_text"] = []

        print("plotting data")

        for _i, tr in enumerate(temp_st):
            self.plot = self.waveform_graph.addPlot(
                _i, 0, title=tr.id,
                axisItems={'bottom': DateAxisItem(orientation='bottom',
                                                  utcOffset=0)})
            self.plot.show()

            # print(self.plot)
            self._state["waveform_plots"].append(self.plot)
            self._state["station_id"].append(tr.stats.network + '.' +
                                             tr.stats.station + '.' +
                                             tr.stats.location + '.' +
                                             tr.stats.channel)
            self._state["station_tag"].append(str(tr.stats.asdf.tag))
            self.plot.plot(tr.times() + tr.stats.starttime.timestamp, tr.data)
            starttimes.append(tr.stats.starttime)
            endtimes.append(tr.stats.endtime)
            min_values.append(tr.data.min())
            max_values.append(tr.data.max())

            # When Mouse is moved over plot print the data coordinates
            # self.plot.scene().sigMouseMoved.connect(self.dispMousePos)


            try:

                if tr.stats.p == 0 or tr.stats.p_as == 0:
                    # no picks for station and earthquake
                    p_as_line = None
                    p_line = None

                    p_as_text = None
                    p_text = None

                else:

                    # plot vertical lines for P theroetical and P-picked arrival
                    p_as_line = pg.PlotCurveItem()
                    p_line = pg.PlotCurveItem()

                    self.plot.addItem(p_as_line)
                    self.plot.addItem(p_line)

                    # text for P and P-as arrivals
                    p_as_text = pg.TextItem("P_as", anchor=(1, 1), color='#ff8000')
                    p_text = pg.TextItem("P", anchor=(1, 1), color='#40ff00')

                    self.plot.addItem(p_as_text)
                    self.plot.addItem(p_text)
            except AttributeError:
                # the is no p arrival time in the ASDF auxillary data
                p_as_line = None
                p_line = None

                p_as_text = None
                p_text = None

            self._state["p_lines"].append(p_line)
            self._state["p_as_lines"].append(p_as_line)

            self._state["p_text"].append(p_text)
            self._state["p_as_text"].append(p_as_text)

            vLine = pg.InfiniteLine(angle=90, movable=True)
            self.plot.addItem(vLine, ignoreBounds=True)

            # when mouse is clicked with modifer then open picker
            # self.plot.scene().sigMouseClicked.connect(self.on_graph_itemClicked)

            # connect with the method to catch when waveform plot is intercated with
            # self.plot.sigRangeChanged.connect(functools.partial(self.waveform_plot_interact, (self.plot, _i)))

            # self.plot.scene().HoverEvent.connect(self.waveform_plot_hovered)

        self.waveform_graph.setNumberPlots(len(temp_st))

        self._state["waveform_plots_min_time"] = min(starttimes)
        self._state["waveform_plots_max_time"] = max(endtimes)
        self._state["waveform_plots_min_value"] = min(min_values)
        self._state["waveform_plots_max_value"] = max(max_values)

        # highlight the plotted region on station availability plot if it exists
        if hasattr(self, 'data_avail_plot'):
            self.data_avail_plot.display_plot_view_region(self._state["waveform_plots_min_time"].timestamp,
                                                          self._state["waveform_plots_max_time"].timestamp)

        for plot in self._state["waveform_plots"][1:]:
            plot.setXLink(self._state["waveform_plots"][0])
            plot.setYLink(self._state["waveform_plots"][0])

        for _i, plot in enumerate(self._state["waveform_plots"]):
            # print(_i)
            # print(plot)

            self.active_plot = plot
            self.active_tr_index = _i
            plot.hideAxis("bottom")
            # connect with the method to catch when waveform plot is intercated with
            # plot.sigRangeChanged.connect(functools.partial(self.waveform_plot_interact, (plot, _i)))
            self.waveform_plot_interact()

        # connect the waveform graph widget to the mouse moved listener
        self.waveform_graph.scene().sigMouseMoved.connect(self.waveform_plot_mouseMoved)

        # connect the active plot to the arrival picker if the mouse is clicked
        self.active_plot.scene().sigMouseClicked.connect(self.waveform_widget_itemClicked)

        # connect with the method to catch when waveform plot is intercated with
        self.active_plot.sigRangeChanged.connect(self.waveform_plot_interact)

        self._state["waveform_plots"][-1].showAxis("bottom")

        self.reset_view()

    def get_current_plot_info(self):
        ids_list = self._state["station_id"]
        tags_list = self._state["station_tag"]

        net_set = set()
        sta_set = set()
        chan_set = set()
        tags_set = set()

        for id in ids_list:
            net, sta, loc, chan = id.split('.')
            net_set.add(net)
            sta_set.add(sta)
            chan_set.add(chan)

        for tag in tags_list:
            tags_set.add(tag)

        return (net_set, sta_set, chan_set, tags_set)

    def on_previous_interval_push_button_released(self):
        # Get start and end time of previous interval with 10% overlap
        starttime = UTCDateTime(self._state["waveform_plots_min_time"])
        endtime = UTCDateTime(self._state["waveform_plots_max_time"])

        delta_time = endtime - starttime
        overlap_time = delta_time * 0.1

        new_start_time = starttime - (delta_time - overlap_time)
        new_end_time = starttime + overlap_time

        net_set, sta_set, chan_set, tags_set = self.get_current_plot_info()

        self.extract_waveform_frm_ASDF(True,
                                       net_list=list(net_set),
                                       sta_list=list(sta_set),
                                       chan_list=list(chan_set),
                                       tags_list=list(tags_set),
                                       ph_st=new_start_time,
                                       ph_et=new_end_time,
                                       xquake=False)

    def on_next_interval_push_button_released(self):
        # Get start and end time of next interval with 10% overlap
        starttime = UTCDateTime(self._state["waveform_plots_min_time"])
        endtime = UTCDateTime(self._state["waveform_plots_max_time"])

        delta_time = endtime - starttime
        overlap_time = delta_time * 0.1

        new_start_time = endtime - (overlap_time)
        new_end_time = endtime + (delta_time - overlap_time)

        net_set, sta_set, chan_set, tags_set = self.get_current_plot_info()

        self.extract_waveform_frm_ASDF(True,
                                       net_list=list(net_set),
                                       sta_list=list(sta_set),
                                       chan_list=list(chan_set),
                                       tags_list=list(tags_set),
                                       ph_st=new_start_time,
                                       ph_et=new_end_time,
                                       xquake=False)

    def on_xcorr_push_button_released(self):
        """
        perform cross correlations of data in view with nearest permenant station data
        :return:
        """
        # Get start and end time of data in view
        starttime = UTCDateTime(self._state["waveform_plots_min_time"])
        endtime = UTCDateTime(self._state["waveform_plots_max_time"])

    def show_provenance_document(self, document_name):
        doc = self.ds.provenance[document_name]
        doc.plot(filename=self._tempfile, use_labels=True)

        self.ui.provenance_graphics_view.open_file(self._tempfile)

    def on_station_view_itemClicked(self, item):
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

        def select_file(ds_id):
            # change the selected file
            self.change_active_ASDF(ds_id)
            for value in self.ASDF_accessor.values():
                # set the color of inactive files to White - default background color
                # value['file_tree_item'].setBackgroundColor(0, QtGui.QColor(255,255,255,0))
                # also disable other items
                value['file_tree_item'].setDisabled(True)

            # set the active file to semi-transparent green and enabled
            self.ASDF_accessor[self.ds_id]['file_tree_item'].setDisabled(False)
            # self.ASDF_accessor[self.ds_id]['file_tree_item'].setBackgroundColor(0, QtGui.QColor(0,0,255,100))

        if t == STATION_VIEW_ITEM_TYPES["FILE"]:
            select_file(str(item.text(0)))
        elif t == STATION_VIEW_ITEM_TYPES["NETWORK"]:
            select_file(str(item.parent().text(0)))
            network = item.text(0)
            js_call = "highlightNetwork('{network}')".format(network=network)
            self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)
        elif t == STATION_VIEW_ITEM_TYPES["STATION"]:
            select_file(str(item.parent().parent().text(0)))
            station = get_station(item, parent=False)
            js_call = "highlightStation('{station}')".format(station=station)
            self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)

            # attempt to unpack children info
            if item.childCount() == 0:
                # get stationxml (to channel level) for station
                print(station)
                station_inv = self.ds.waveforms[station].StationXML[0][0]
                print(station_inv)

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

                item.addChildren(station_children)

                # add channel items
                for channel_inv in station_inv:
                    channel_item = QtGui.QTreeWidgetItem(
                        [channel_inv.code], type=STATION_VIEW_ITEM_TYPES["CHANNEL"])

                    channel_children = [
                        QtGui.QTreeWidgetItem(
                            ['StartDate: \t%s' % channel_inv.start_date.strftime('%Y-%m-%dT%H:%M:%S')],
                            type=STATION_VIEW_ITEM_TYPES["CHAN_INFO"]),
                        QtGui.QTreeWidgetItem(['EndDate: \t%s' % channel_inv.end_date.strftime('%Y-%m-%dT%H:%M:%S')],
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

                    item.addChild(channel_item)


        elif t == STATION_VIEW_ITEM_TYPES["CHANNEL"]:
            select_file(str(item.parent().parent().parent().text(0)))
            station = get_station(item)
            js_call = "highlightStation('{station}')".format(station=station)
            self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)
        elif t == STATION_VIEW_ITEM_TYPES["CHAN_INFO"]:
            select_file(str(item.parent().parent().parent().parent().text(0)))
            station = get_station(item)
            js_call = "highlightStation('{station}')".format(station=station)
            self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)
        elif t == STATION_VIEW_ITEM_TYPES["STN_INFO"]:
            select_file(str(item.parent().parent().parent().text(0)))
            station = get_station(item)
            js_call = "highlightStation('{station}')".format(station=station)
            self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)
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
            net = str(item.text(0))

            # get the start and end date of network
            # we need to get a station that is part of teh network we are after and then get StationXML
            for station in self.ds.ifilter(self.ds.q.network == net):
                inv = station.StationXML
                break

            net_st = inv[0].start_date
            net_et = inv[0].end_date

            print(net_st)

            net_list = [net]
            net_sta_list = self.ASDF_accessor[self.ds_id]['net_sta_list']
            # create station list with just station names without network code
            sta_list = [x.split('.')[1] for x in net_sta_list]

            chan_list = self.ASDF_accessor[self.ds_id]['channel_codes']
            tags_list = self.ASDF_accessor[self.ds_id]['tags_list']

            self.net_item_menu = QtGui.QMenu(self)
            select_action = QtGui.QAction('Select NSCL', self)
            select_action.triggered.connect(lambda: self.extract_waveform_frm_ASDF(False,
                                                                                   net_list=net_list,
                                                                                   sta_list=sta_list,
                                                                                   chan_list=chan_list,
                                                                                   tags_list=tags_list,
                                                                                   ph_st=str(net_st).split('.')[0],
                                                                                   ph_et=
                                                                                   str(net_st + 60 * 60).split('.')[0],
                                                                                   xquake=False))

            self.net_item_menu.addAction(select_action)
            self.net_item_menu.exec_(self.ui.station_view.viewport().mapToGlobal(position))

        elif t == STATION_VIEW_ITEM_TYPES["STATION"]:
            station = get_station(item)
            # make sure JSON DB is loaded in
            if not self.db:
                print("No DB is Loaded!!")
                return

            net_list = [station.split('.')[0]]
            sta_list = [station.split('.')[1]]

            # get station accessor for ASDF
            sta = self.ds.waveforms[station]

            # get inventory for station
            inv = sta.StationXML

            net_st = inv[0][0].start_date
            net_et = inv[0][0].end_date

            print(net_st)

            chan_list = [x.split('.')[2] for x in inv[0][0].get_contents()["channels"]]

            tags_list = sta.get_waveform_tags()

            # Run Method to create ASDF SQL database with SQLite (one db per station within ASDF)
            # self.create_asdf_sql(station)

            self.net_item_menu = QtGui.QMenu(self)

            # extract waveforms for station action
            select_action = QtGui.QAction('Extract Waveforms for Station', self)
            select_action.triggered.connect(lambda: self.extract_waveform_frm_ASDF(False,
                                                                                   net_list=net_list,
                                                                                   sta_list=sta_list,
                                                                                   chan_list=chan_list,
                                                                                   tags_list=tags_list,
                                                                                   ph_st=str(net_st).split('.')[0],
                                                                                   ph_et=
                                                                                   str(net_st + 60 * 60).split('.')[0],
                                                                                   xquake=False))

            self.net_item_menu.addAction(select_action)

            # station trace explorer action
            trace_explore_action = QtGui.QAction('Trace Explorer', self)
            trace_explore_action.triggered.connect(lambda: self.trace_explorer(sta))

            self.net_item_menu.addAction(trace_explore_action)

            self.net_item_menu.exec_(self.ui.station_view.viewport().mapToGlobal(position))

        elif t == STATION_VIEW_ITEM_TYPES["CHANNEL"]:
            station_item = item.parent()
            station = get_station(station_item)
            channel = item.text(0)

            net_list = [station.split('.')[0]]
            sta_list = [station.split('.')[1]]
            chan_list = [channel]

            # get station accessor for ASDF
            sta = self.ds.waveforms[station]

            tags_list = sta.get_waveform_tags()

            # get inventory for station
            inv = sta.StationXML

            print(inv[0][0][0])

            net_st = inv[0][0][0].start_date
            net_et = inv[0][0][0].end_date

            if not net_st or not net_et:
                # there is no time information for the channel
                # get the start and end time for the station
                net_st = inv[0][0].start_date
                net_et = inv[0][0].end_date

            # set the rounded to minute default time of extraction
            # default is 1 hour after start of data or from start to end of data if shorter than 1 hour
            ph_start = str(net_st).split('.')[0]
            ph_end = str(net_st + 60 * 60).split('.')[0]

            self.net_item_menu = QtGui.QMenu(self)
            select_action = QtGui.QAction('Extract Waveforms for Channel', self)
            select_action.triggered.connect(lambda: self.extract_waveform_frm_ASDF(False,
                                                                                   net_list=net_list,
                                                                                   sta_list=sta_list,
                                                                                   chan_list=chan_list,
                                                                                   tags_list=tags_list,
                                                                                   ph_st=ph_start,
                                                                                   ph_et=ph_end,
                                                                                   xquake=False))

            self.net_item_menu.addAction(select_action)
            self.net_item_menu.exec_(self.ui.station_view.viewport().mapToGlobal(position))

    def on_station_view_itemEntered(self, item):
        # TODO: fix station highlighting on hover
        # TODO: fix station popup label on map
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

        if t == STATION_VIEW_ITEM_TYPES["FILE"]:
            pass
        elif t == STATION_VIEW_ITEM_TYPES["NETWORK"]:
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

        rf_action = QtGui.QAction('Calculate Receiver Function', self)
        # Connect the triggered menu object to a function passing an extra variable
        rf_action.triggered.connect(lambda: self.calculate_RF(obj))
        self.event_item_menu.addAction(rf_action)

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

        # attempt to unpack children
        if item.childCount() == 0 and t != AUX_DATA_ITEM_TYPES["DATA_ITEM"]:
            data_type = item.parent().text(0)
            path_lev_zero = item.text(0)

            sub_items = []

            # run the recursive function to unpack all sub children and data
            for sub_data in self.ds.auxiliary_data[data_type][path_lev_zero].list():
                sub_items.append(recursive_tree(sub_data, self.ds.auxiliary_data[data_type][path_lev_zero][sub_data]))

            item.addChildren(sub_items)

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
            try:
                img = pg.ImageItem(border="#3D8EC9")
                img.setImage(aux_data.data.value)
                vb = graph.addViewBox()
                vb.setAspectLocked(True)
                vb.addItem(img)
                self.ui.auxiliary_data_stacked_widget.setCurrentWidget(
                    self.ui.auxiliary_data_graph_page)
            except ValueError:
                pass
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

    def trace_explorer(self, sta_accessor):
        """
        Method to look at traces for a selected station in a table view
        Traces can then be plotted
        :param sta_accessor: the station accessor object
        :return:
        """

        self.sta_accessor = sta_accessor

        # get a list of all waveforms in ASDF file
        trace_list = self.sta_accessor.list()
        # remove the station XML entry
        trace_list.remove("StationXML")

        # now make pandas dataframe sorted by startdate and split into information
        # create empty data frame
        self.trace_df = pd.DataFrame(data=None,
                                     columns=['ASDF_id', 'id', 'channel', 'start_UTC', 'end_UTC', 'start_timestamp',
                                              'end_timestamp', 'tag'])

        # iterate through trace_list
        for _i, trace in enumerate(trace_list):
            info = trace.split('__')
            id = info[0]
            channel = id.split('.')[3]

            start_timestamp = UTCDateTime(info[1]).timestamp
            end_timestamp = UTCDateTime(info[2]).timestamp

            tag = info[3]

            self.trace_df.loc[_i] = [trace, id, channel, UTCDateTime(start_timestamp).ctime(),
                                     UTCDateTime(end_timestamp).ctime(), start_timestamp, end_timestamp, tag]

        self.trace_df.sort_values(by='start_UTC', inplace=True)
        self.trace_df.reset_index(drop=True, inplace=True)
        print(self.trace_df)

        # create table in new window
        self.trace_tbld = TraceTableDialog(parent=self, trace_df=self.trace_df)

        # make trace table right clickable for plotting
        self.trace_tbld.trace_table_view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.trace_tbld.trace_table_view.customContextMenuRequested.connect(self.trace_tbl_view_popup)

    def query_to_stream(self, query, interval_tuple):
        """
        method to use output from seisds query to return waveform streams
        """

        # Open a new st object
        st = Stream()

        for matched_info in query.values():
            # self.db.retrieve_full_db_entry(matched_info["ASDF_tag"])

            # read the data from the ASDF into stream
            temp_tr = self.ds.waveforms[matched_info["new_network"] + '.' + matched_info["new_station"]][
                matched_info["ASDF_tag"]][0]

            # trim trace to start and endtime
            temp_tr.trim(starttime=UTCDateTime(interval_tuple[0]), endtime=UTCDateTime(interval_tuple[1]))

            # append the asdf id tag into the trace stats so that the original data is accesbale
            temp_tr.stats.asdf.orig_id = matched_info["ASDF_tag"]

            # append trace to stream
            st += temp_tr

            # free memory
            temp_tr = None

        try:

            if st.__nonzero__():
                # Attempt to merge all traces with matching ID'S in place
                print('')
                print('Merging Traces from %s Stations....' % len(st))
                # filling no data with 0
                st.merge(fill_value=0)
                print('\nTrimming Traces to specified time interval....')
                st.trim(starttime=UTCDateTime(interval_tuple[0]), endtime=UTCDateTime(interval_tuple[1]))
                return st
            else:
                return None
        except UnboundLocalError:
            # the station selection dialog box was cancelled
            return None

    def extract_waveform_frm_ASDF(self, override, **kwargs):
        # # Open a new st object
        # self.st = Stream()

        # print(kwargs["event_id"])
        # print(kwargs["event_df"])

        net_list = kwargs['net_list']
        sta_list = kwargs['sta_list']
        chan_list = kwargs['chan_list']
        tags_list = kwargs['tags_list']
        ph_st = kwargs["ph_st"]
        ph_et = kwargs["ph_et"]
        xquake = kwargs["xquake"]

        # If override flag then we are calling this
        # method by using prev/next interval buttons
        # I.e. dont bring up the station selection dialog pop-up - just use get whatever is in the current view
        if override:
            file_output = False
            ref_stn_out = False
            interval_tuple = (ph_st.timestamp, ph_et.timestamp)
            query = self.db.queryByTime(net_list, sta_list, chan_list, tags_list, interval_tuple[0], interval_tuple[1])

            self.st = self.query_to_stream(query, interval_tuple)

        # if there is no override flag then we want to extract data from a desired net/sta/chan and time interval
        # i.e. show the selection dialog
        elif not override:

            # now call station and channel selection dialog box
            sel_dlg = selectionDialog(parent=self, net_list=net_list, sta_list=sta_list, chan_list=chan_list,
                                      tags_list=tags_list, ph_start=ph_st, ph_end=ph_et, xquake=xquake)
            if sel_dlg.exec_():
                select_net, select_sta, select_chan, select_tags, st, et, file_output, ref_stn_out, \
                bef_quake_xt, aft_quake_xt = sel_dlg.getSelected()

                if xquake == True:
                    # we are lokking at an earthquake, adjust the extraction time based on spin box values
                    interval_tuple = (st.timestamp - bef_quake_xt, et.timestamp + aft_quake_xt)
                else:
                    interval_tuple = (st.timestamp, et.timestamp)

                print('---------------------------------------')
                print('Finding Data for specified time interval.....')

                # print(select_net)
                # print(select_sta)
                # print(select_chan)
                # print(select_tags)
                # print(UTCDateTime(interval_tuple[0]))
                # print(UTCDateTime(interval_tuple[1]))

                print(select_net, select_sta, select_chan, select_tags, UTCDateTime(interval_tuple[0]),
                      UTCDateTime(interval_tuple[1]))

                query = self.db.queryByTime(select_net, select_sta, select_chan, select_tags, interval_tuple[0],
                                            interval_tuple[1])

                self.st = self.query_to_stream(query, interval_tuple)

                print(self.st)





            else:
                return

        if not self.st == None:
            # add in p_arr and p_as_arrival attributes in the trace stats as 0
            for tr in self.st:
                tr.stats.p = 0
                tr.stats.p_as = 0

            self.update_waveform_plot()
            # Now output data into new ASDF if required
            if file_output:
                self.initiate_output_eq_asdf()

                event = self.cat[self.cat_row_index]
                self.keys_list = []
                self.info_list = []

                if ref_stn_out:
                    self.retrieve_ref_data(interval_tuple, event)

                self.output_event_asdf(event)

                big_dictionary = dict(zip(self.keys_list, self.info_list))

                with open(self.json_out, 'w') as fp:
                    json.dump(big_dictionary, fp)

                # print(self.st)
                # print(self.selected_row)

                # print(self.cat)
                # print(self.cat[self.cat_row_index])

                # close the dataset
                del self.out_eq_asdf

        else:
            msg = QtGui.QMessageBox()
            msg.setIcon(QtGui.QMessageBox.Critical)
            msg.setText("No Data for Requested Time Interval")
            msg.setDetailedText("There are no waveforms to display for selected time interval:"
                                "\nStart Time = " + str(UTCDateTime(interval_tuple[0], precision=0)) +
                                "\nEnd Time =   " + str(UTCDateTime(interval_tuple[1], precision=0)))
            msg.setWindowTitle("Extract Time Error")
            msg.setStandardButtons(QtGui.QMessageBox.Ok)
            msg.exec_()

    def extract_multi_quakes(self, mode):
        """
        Method to extract multiple earthquakes from the ASDF for multiple stations and can include reference stations
        Then save all of that data into a new ASDF file
        """
        focus_widget = self.tbl_view_dict["cat"]

        if mode == "selected":
            # get the selected row numbers
            row_number_list = [x.row() for x in focus_widget.selectionModel().selectedRows()]
            self.cat_row_index_list = [self.table_accessor[focus_widget][1][x] for x in row_number_list]
            print(self.cat_row_index_list)


        elif mode == "all":
            self.cat_row_index_list = self.table_accessor[focus_widget][1]

        self.selected_row_list = [self.cat_df.loc[x] for x in self.cat_row_index_list]

        net_sta_list = self.ASDF_accessor[self.ds_id]['net_sta_list']

        # get a list of unique networks and stations
        net_list = list(set([x.split('.')[0] for x in net_sta_list]))
        sta_list = [x.split('.')[1] for x in net_sta_list]

        chan_list = self.ASDF_accessor[self.ds_id]['channel_codes']
        tags_list = self.ASDF_accessor[self.ds_id]['tags_list']

        # open up dialog box to select stations/channels etc to extract earthquakes
        sel_dlg = selectionDialog(parent=self, net_list=net_list, sta_list=sta_list, chan_list=chan_list,
                                  tags_list=tags_list, xquake=True)
        if sel_dlg.exec_():
            select_net, select_sta, select_chan, select_tags, st, et, file_output, ref_stn_out, \
            bef_quake_xt, aft_quake_xt = sel_dlg.getSelected()

            self.initiate_output_eq_asdf()
            self.keys_list = []
            self.info_list = []

            for _i, sel_quake in enumerate(self.selected_row_list):
                print("Extracting Earthquake ", _i + 1, " of ", len(self.cat_row_index_list))
                qtime = sel_quake['qtime']
                event = self.cat[self.cat_row_index_list[_i]]

                # we are looking at an earthquake, adjust the extraction time based on spin box values
                interval_tuple = (qtime - bef_quake_xt, qtime + aft_quake_xt)
                # do a query for data
                query = self.db.queryByTime(select_net, select_sta, select_chan, select_tags, interval_tuple[0],
                                            interval_tuple[1])
                self.st = self.query_to_stream(query, interval_tuple)

                if not self.st == None:

                    if ref_stn_out:
                        self.retrieve_ref_data(interval_tuple, event)

                    self.output_event_asdf(event)

                else:
                    continue

            big_dictionary = dict(zip(self.keys_list, self.info_list))

            with open(self.json_out, 'w') as fp:
                json.dump(big_dictionary, fp)

            # close the dataset
            del self.out_eq_asdf

    def get_associated_stations_for_quake(self, event_id):
        net_set = set()
        sta_set = set()
        chan_set = set()

        # get a list of net_sta that have data for an event using what is stored in the auxillary data
        event_net_sta_list = self.ds.auxiliary_data.ArrivalData[event_id].list()

        # create station list with just station names without network code
        for x in event_net_sta_list:
            sta_set.add(x.split('_')[1])
            net_set.add(x.split('_')[0])
            chan_set.add(x.split('_')[3])

        net_list = list(net_set)
        sta_list = list(sta_set)
        chan_list = list(chan_set)

        tags_list = self.ASDF_accessor[self.ds_id]['tags_list']

        return (net_list, sta_list, chan_list, tags_list)

    def calculate_RF(self, event_obj):
        # Get event catalogue
        # self.event_cat = self.ds.events

        print(event_obj)

        # open up dialog of where to save rf ASDF file
        self.out_rf_filename = str(QtGui.QFileDialog.getSaveFileName(
            parent=self, caption="Output Receiver Function ASDF file",
            directory=os.path.expanduser("~")))
        if not self.out_rf_filename:
            return

        # make correct extension
        if '.' in self.out_rf_filename:
            self.out_rf_filename = self.out_rf_filename.split(".")[0] + ".h5"
        else:
            self.out_rf_filename = self.out_rf_filename + ".h5"

        # remove the file if it already exists
        if os.path.exists(self.out_rf_filename):
            os.remove(self.out_rf_filename)

        tmp_out_rf = join(dirname(self.out_rf_filename), "tmp_rf.h5")

        if event_obj != None:
            event_res_id = str(event_obj.resource_id.id)
        else:
            event_res_id = None

        asdf_rf_calc(self.ds, tmp_out_rf, event_res_id)
        clean_rf_ds(self.ds, tmp_out_rf, self.out_rf_filename)

    def analyse_earthquake(self, event_obj):
        # Get event catalogue
        self.event_cat = self.ds.events

        print(event_obj)

        event_id = str(event_obj.resource_id.id).split('=')[1]

        # get the row index in the data frame
        self.select_quake = self.cat_df.loc[self.cat_df['event_id'] == event_id]
        event_row_index = self.select_quake.index.tolist()[0]

        self.EQ_table_view_highlight(self.EQtbld.tbldui.EQ_xtract_tableView, event_row_index)

        net_list, sta_list, chan_list, tags_list = self.get_associated_stations_for_quake(event_id)

        # Launch the custom station/component selection dialog
        sel_dlg = selectionDialog(parent=self, net_list=net_list, sta_list=sta_list, chan_list=chan_list,
                                  tags_list=tags_list)
        if sel_dlg.exec_():
            select_net, select_sta, select_chan, select_tags = sel_dlg.getSelected()

            # Open up a new stream object
            self.st = Stream()

            # use the ifilter functionality to extract desired streams to visualize
            for station in self.ds.ifilter(self.ds.q.station == select_sta,
                                           self.ds.q.channel == select_chan,
                                           self.ds.q.event == event_obj):
                for filtered_id in station.list():
                    if filtered_id == 'StationXML':
                        continue
                    self.st += station[filtered_id]

            #
            if self.st.__nonzero__():
                print(self.st)
                # Get quake origin info
                origin_info = event_obj.preferred_origin() or event_obj.origins[0]

                # Iterate through traces
                for tr in self.st:
                    # Run Java Script to highlight all selected stations in station view
                    js_call = "highlightStation('{station}')".format(station=tr.stats.network + '.' + tr.stats.station)
                    self.ui.web_view.page().mainFrame().evaluateJavaScript(js_call)

                    # Get inventory for trace
                    # inv = self.ds.waveforms[tr.stats.network + '.' +tr.stats.station].StationXML

                    # sta_coords = inv.get_coordinates(tr.get_id())
                    #
                    # dist, baz, _ = gps2dist_azimuth(sta_coords['latitude'],
                    #                                 sta_coords['longitude'],
                    #                                 origin_info.latitude,
                    #                                 origin_info.longitude)
                    # dist_deg = kilometer2degrees(dist/1000.0)
                    # tt_model = TauPyModel(model='iasp91')
                    # arrivals = tt_model.get_travel_times(origin_info.depth/1000.0, dist_deg, ('P'))

                    arrival_aux = self.ds.auxiliary_data.ArrivalData[event_id][tr.get_id().replace(".", "_")]
                    print(arrival_aux)

                    # Write info to trace header
                    tr.stats.distance = float(arrival_aux.parameters["distkm"])
                    tr.stats.p = UTCDateTime(arrival_aux.parameters["P"]).timestamp
                    tr.stats.p_as = UTCDateTime(arrival_aux.parameters["P_as"]).timestamp

                # Sort the st by distance from quake
                self.st.sort(keys=['distance'])

                self.update_waveform_plot()

    def make_ASDF_tag(self, tr, tag):
        # function to create the ASDF waveform ID tag
        #  def make_ASDF_tag(ri, tag):
        data_name = "{net}.{sta}.{loc}.{cha}__{start}__{end}__{tag}".format(
            net=tr.stats.network,
            sta=tr.stats.station,
            loc=tr.stats.location,
            cha=tr.stats.channel,
            start=tr.stats.starttime.strftime("%Y-%m-%dT%H:%M:%S"),
            end=tr.stats.endtime.strftime("%Y-%m-%dT%H:%M:%S"),
            tag=tag)
        return data_name

    def initiate_output_eq_asdf(self):
        print("Outputting Data into ASDF file")
        # open up dialog of where to save earthquake ASDF file
        self.out_eq_filname = str(QtGui.QFileDialog.getSaveFileName(
            parent=self, caption="Output EQ ASDF file",
            directory=os.path.expanduser("~")))
        if not self.out_eq_filname:
            return

        # make correct estension
        if '.' in self.out_eq_filname:
            self.out_eq_filname = self.out_eq_filname.split(".")[0] + ".h5"
        else:
            self.out_eq_filname = self.out_eq_filname + ".h5"

        # remove the file if it already exists
        if os.path.exists(self.out_eq_filname):
            os.remove(self.out_eq_filname)

        # output json filename
        self.json_out = self.out_eq_filname.split(".")[0] + ".json"

        # remove if exists
        if os.path.exists(self.json_out):
            os.remove(self.json_out)

        # print(self.out_eq_filname)


        # create the asdf file
        self.out_eq_asdf = pyasdf.ASDFDataSet(self.out_eq_filname)

    def retrieve_ref_data(self, interval_tuple, event):
        # Get quake origin info
        origin_info = event.preferred_origin() or event.origins[0]
        event_id = str(event.resource_id.id).split('=')[1]

        station_coords = self.ASDF_accessor[self.ds_id]['station_coords']

        # request stations that are close to the selected stations

        # first use the coords lists to get a bounding box for array
        def calc_bounding_box(x, y):
            min_x, max_x = (min(x), max(x))
            min_y, max_y = (min(y), max(y))

            return (min_x, max_x, min_y, max_y)

        bb = calc_bounding_box(station_coords[0], station_coords[1])

        # request data for near earthquake time up to 5 degrees from bounding box of array
        print('\nRequesting Waveform Data from Nearby Permanent Network Stations....')

        client = Client("IRIS")
        ref_inv = client.get_stations(network="AU",
                                      starttime=interval_tuple[0],
                                      endtime=interval_tuple[1],
                                      minlongitude=bb[0] - 2,
                                      maxlongitude=bb[1] + 2,
                                      minlatitude=bb[2] - 2,
                                      maxlatitude=bb[3] + 2,
                                      level='channel')

        ref_data = []

        # go through inventory and request timeseries data
        for net in ref_inv:
            for stn in net:
                try:
                    ref_st = client.get_waveforms(network=net.code, station=stn.code, channel='*', location='*',
                                                  starttime=interval_tuple[0],
                                                  endtime=interval_tuple[1])

                except FDSNException:
                    print('No Data for Earthquake from Reference Station: ' + stn.code)


                else:
                    # append the inv and st
                    ref_data.append({"ref_st": ref_st, "ref_inv": ref_inv.select(station=stn.code)})

        # add the data into output ASDF file
        for ref_stn_data in ref_data:
            ref_st = ref_stn_data["ref_st"]
            ref_inv = ref_stn_data["ref_inv"]
            for tr in ref_st:
                print(tr)
                # The ASDF formatted waveform name [full_id, station_id, starttime, endtime, tag]
                ASDF_tag = self.make_ASDF_tag(tr, "earthquake").encode('ascii')

                # make a dictionary for the trace that will then be appended to a larger dictionary for whole network
                temp_dict = {"tr_starttime": tr.stats.starttime.timestamp,
                             "tr_endtime": tr.stats.endtime.timestamp,
                             "orig_network": tr.stats.network,
                             "new_network": tr.stats.network,
                             "orig_station": tr.stats.station,
                             "new_station": tr.stats.station,
                             "orig_channel": tr.stats.channel,
                             "new_channel": tr.stats.channel,
                             "orig_location": tr.stats.location,
                             "new_location": tr.stats.location,
                             "seed_path": "",
                             "seed_filename": "",
                             "log_filename": ""}

                self.keys_list.append(str(ASDF_tag))
                self.info_list.append(temp_dict)

                print(ref_inv)

                self.out_eq_asdf.add_stationxml(ref_inv)

                # calculate the p-arrival time
                sta_coords = (ref_inv[0][0].latitude, ref_inv[0][0].longitude, ref_inv[0][0].elevation)
                print(sta_coords)

                dist, baz, _ = gps2dist_azimuth(sta_coords[0],
                                                sta_coords[1],
                                                origin_info.latitude,
                                                origin_info.longitude)
                dist_deg = kilometer2degrees(dist / 1000.0)
                tt_model = TauPyModel(model='iasp91')
                arrivals = tt_model.get_travel_times(origin_info.depth / 1000.0, dist_deg, ('P'))

                # make parametric data such as expected earthquake arrival time and spce to pick arrivals
                # store in ASDF auxillary data

                if len(arrivals) == 0:
                    # there is no P arrival (i.e. earthquake is other side of earth
                    p_time = 0
                else:
                    p_time = arrivals[0].time

                parameters = {"P": str(origin_info.time + p_time),
                              "P_as": str(origin_info.time + p_time - 60 * 3),
                              "distkm": dist / 1000.0,
                              "dist_deg": dist_deg}

                data_type = "ArrivalData"
                data_path = event_id + "/" + tr.get_id().replace('.', '_')

                # add the waveforms referenced to the earthquake
                self.out_eq_asdf.add_waveforms(tr, tag="earthquake",
                                               event_id=event)
                self.out_eq_asdf.add_auxiliary_data(data=np.array([0]),
                                                    data_type=data_type,
                                                    path=data_path,
                                                    parameters=parameters)

    def output_event_asdf(self, event):
        # Get quake origin info
        origin_info = event.preferred_origin() or event.origins[0]
        event_id = str(event.resource_id.id).split('=')[1]

        for tr in self.st:
            print(tr)
            # The ASDF formatted waveform name [full_id, station_id, starttime, endtime, tag]
            ASDF_tag = self.make_ASDF_tag(tr, "earthquake").encode('ascii')

            # get the json dict entry for the original trace
            temp_dict = self.db.retrieve_full_db_entry(tr.stats.asdf.orig_id)

            # modify the start and end times of the trace ti be correct
            temp_dict["tr_starttime"] = tr.stats.starttime.timestamp
            temp_dict["tr_endtime"] = tr.stats.endtime.timestamp

            self.keys_list.append(str(ASDF_tag))
            self.info_list.append(temp_dict)

            inv = self.ds.waveforms[tr.stats.network + "." + tr.stats.station].StationXML
            self.out_eq_asdf.add_stationxml(inv)

            # calculate the p-arrival time
            sta_coords = inv.get_coordinates(tr.get_id())

            dist, baz, _ = gps2dist_azimuth(sta_coords['latitude'],
                                            sta_coords['longitude'],
                                            origin_info.latitude,
                                            origin_info.longitude)
            dist_deg = kilometer2degrees(dist / 1000.0)
            tt_model = TauPyModel(model='iasp91')
            arrivals = tt_model.get_travel_times(origin_info.depth / 1000.0, dist_deg, ('P'))

            # make parametric data such as expected earthquake arrival time and spce to pick arrivals
            # store in ASDF auxillary data

            data_type = "ArrivalData"
            data_path = event_id + "/" + tr.get_id().replace('.', '_')

            if len(arrivals) == 0:
                # there is no P arrival (i.e. earthquake is other side of earth
                p_time = 0
            else:
                p_time = arrivals[0].time

            parameters = {"P": str(origin_info.time + p_time),
                          "P_as": str(origin_info.time + p_time - 60 * 3),
                          "distkm": dist / 1000.0,
                          "dist_deg": dist_deg}

            # add the waveforms referenced to the earthquake
            self.out_eq_asdf.add_waveforms(tr, tag="earthquake",
                                           event_id=event)
            self.out_eq_asdf.add_auxiliary_data(data=np.array([0]),
                                                data_type=data_type,
                                                path=data_path,
                                                parameters=parameters)
            # print(tr.stats.network + "." + tr.stats.station)

        # add the earthquake
        self.out_eq_asdf.add_quakeml(event)

    def station_availability(self):

        # go through JSON entries and find all gaps save them into dictionary
        # self.recording_gaps = {}
        self.recording_intervals = {}
        # self.recording_overlaps = {}

        # print(self.ds.auxiliary_data)

        print('_________________')

        print("\nUsing DataBase to find data gaps, overlaps and recording intervals")

        net_sta_list = self.ds.waveforms.list()

        # make list with station codes and network codes seperated
        net_list = list(set([x.split('.')[0] for x in net_sta_list]))
        sta_list = [x.split('.')[1] for x in net_sta_list]
        chan_list = self.ASDF_accessor[self.ds_id]['channel_codes']
        tags_list = self.ASDF_accessor[self.ds_id]['tags_list']

        # open up the selection dialog for the user to select which data to display availability info
        # Launch the custom station/component selection dialog
        sel_dlg = selectionDialog(parent=self, net_list=net_list, sta_list=sta_list, chan_list=chan_list,
                                  tags_list=tags_list, gaps_analysis=True)
        if sel_dlg.exec_():
            # there will only be one chan and one tag selected
            select_net, select_sta, select_chan, select_tags = sel_dlg.getSelected()

            for net_sta in net_sta_list:

                if net_sta.split('.')[0] in select_net and net_sta.split('.')[1] in select_sta:
                    # get the recording intervals of the station for the selected channel and tag
                    intervals_array = self.db.get_recording_intervals(net_sta.split('.')[0], net_sta.split('.')[1],
                                                                      select_chan[0], select_tags[0])

                    # intervals_no = intervals_array.shape[1]

                    self.recording_intervals[net_sta] = intervals_array

            # if there is an earthquake catalogue loaded then plot the arthquakes on the station availabilty plot
            if hasattr(self, "cat_df"):

                self.data_avail_plot = DataAvailPlot(parent=self, net_list=net_list, sta_list=sta_list,
                                                     chan_list=select_chan, tags_list=tags_list,
                                                     rec_int_dict=self.recording_intervals, cat_avail=True,
                                                     cat_df=self.cat_df)
            else:
                self.data_avail_plot = DataAvailPlot(parent=self, net_list=net_list, sta_list=sta_list,
                                                     chan_list=select_chan, tags_list=tags_list,
                                                     rec_int_dict=self.recording_intervals)

            # connect to the go button in plot
            self.data_avail_plot.davailui.go_push_button.released.connect(self.plot_intervals_selected)


            # # now calculate recording intervals and gaps for all stations
            # for _i, net_sta in enumerate(net_sta_list):
            #     stnxml = self.ds.waveforms[net_sta].StationXML
            #     # get the start recording interval
            #     #  and get the end recording interval
            #     try:
            #         rec_start = UTCDateTime(stnxml[0][0].start_date).timestamp or \
            #                     UTCDateTime(stnxml[0][0].creation_date).timestamp
            #         rec_end = UTCDateTime(stnxml[0][0].end_date).timestamp or \
            #                   UTCDateTime(stnxml[0][0].termination_date).timestamp
            #     except AttributeError:
            #         print("No start/end dates found in XML")
            #         break
            #
            #     print("\r Working on Station: " + net_sta + ", " + str(_i + 1) + " of " + \
            #                   str(len(net_sta_list)) + " Stations", )
            #     sys.stdout.flush()
            #
            #     gaps_array = self.db.get_recording_intervals(net=net_sta.split('.')[0],sta = net_sta.split('.')[1], chan = select_chan, tags=select_tags)
            #
            #     self.recording_gaps[net_sta] = gaps_array
            #
            #     temp_start_int = []
            #     temp_end_int = []
            #
            #     gaps_no = gaps_array.shape[1]
            #
            #     prev_endtime = ''
            #
            #     if gaps_no == 0:
            #         temp_start_int.append(rec_start)
            #         temp_end_int.append(rec_end)
            #     else:
            #         # populate the recording intervals dictionary
            #         for _j in range(gaps_no):
            #             gap_start = gaps_array[0, _j]
            #             gap_end = gaps_array[1, _j]
            #
            #             if _j == 0:
            #                 # first interval
            #                 # print(UTCDateTime(rec_start).ctime(), UTCDateTime(gap_entry['gap_start']).ctime())
            #                 temp_start_int.append(rec_start)
            #                 temp_end_int.append(gap_start)
            #                 prev_endtime = gap_end
            #
            #             if _j == gaps_no - 1:
            #                 # last interval
            #                 # print(UTCDateTime(gap_entry['gap_end']).ctime(), UTCDateTime(rec_end).ctime())
            #                 temp_start_int.append(gap_end)
            #                 temp_end_int.append(rec_end)
            #
            #             elif not _j == 0 and not _j == gaps_no - 1:
            #                 # print(UTCDateTime(gaps_list[_j-1]['gap_end']).ctime(), UTCDateTime(gap_entry['gap_start']).ctime())
            #                 temp_start_int.append(prev_endtime)
            #                 temp_end_int.append(gap_start)
            #                 prev_endtime = gap_end
            #
            #     # the [1] element of shape is the number of intervals
            #     rec_int_array = np.array([temp_start_int, temp_end_int])
            #     self.recording_intervals[net_sta] = rec_int_array
            #
            #     # if there is an earthquake catalogue loaded then plot the arthquakes on the station availabilty plot
            #     if hasattr(self, "cat_df"):
            #
            #         self.data_avail_plot = DataAvailPlot(parent=self, net_list=net_list, sta_list=sta_list,
            #                                          chan_list=select_chan, tags_list=tags_list,
            #                                          rec_int_dict=self.recording_intervals, cat_avail=True, cat_df=self.cat_df)
            #     else:
            #         self.data_avail_plot = DataAvailPlot(parent=self, net_list=net_list, sta_list=sta_list,
            #                                          chan_list=select_chan, tags_list=tags_list,
            #                                          rec_int_dict=self.recording_intervals)
            #
            #     # connect to the go button in plot
            #     self.data_avail_plot.davailui.go_push_button.released.connect(self.intervals_selected)

            #
            # # iterate through stations
            # for _i, net_sta in enumerate(net_sta_list):
            #     # stnxml = self.ds.waveforms[station].StationXML
            #     # # get the start recording interval
            #     # #  and get the end recording interval
            #     # try:
            #     #     rec_start = UTCDateTime(stnxml[0][0].start_date).timestamp or \
            #     #                 UTCDateTime(stnxml[0][0].creation_date).timestamp
            #     #     rec_end = UTCDateTime(stnxml[0][0].end_date).timestamp or \
            #     #               UTCDateTime(stnxml[0][0].termination_date).timestamp
            #     # except AttributeError:
            #     #     print("No start/end dates found in XML")
            #     #     break
            #
            #     # if station == "7D.CZ40":
            #     #     print("rec_start:", UTCDateTime(rec_start))
            #     #     print("rec_end:", UTCDateTime(rec_end))
            #
            #     # get the channels for that station
            #     # xml_list = stnxml.select(channel="*Z").get_contents()['channels']
            #
            #     # sta = str(xml_list[0]).split('.')[1]
            #     # chan = str(xml_list[0]).split('.')[3]
            #
            #     # # the auxillary data hierarchy
            #     # data_type = "StationAvailability"
            #     # gaps_path = station.replace('.', '_') + '/DataGaps'
            #     # # ovlps_path = station.replace('.', '_') + '/DataOverlaps'
            #     # rec_int_path = station.replace('.', '_') + '/RecordingIntervals'
            #     #
            #     # # check if there is already info in auxillary data
            #     # try:
            #     #     aux_gaps = self.ds.auxiliary_data[data_type][station.replace('.', '_')]["DataGaps"].data
            #     #     aux_rec_ints = self.ds.auxiliary_data[data_type][station.replace('.', '_')]["RecordingIntervals"].data
            #     # except KeyError:
            #     #     # no gaps/interval info stored in auxillary data
            #     #     pass
            #     # else:
            #     #     print("Gaps and recording interval information already in "
            #     #           "ASDF Auxillary Data for Station: %s ....." % station)
            #     #     self.recording_intervals[station] = aux_rec_ints
            #     #     self.recording_gaps[station] = aux_gaps
            #     #
            #     #     continue
            #
            #     print("\r Working on Station: " + net_sta+ ", " + str(_i + 1) + " of " + \
            #           str(len(net_sta_list)) + " Stations", )
            #     sys.stdout.flush()
            #
            #     gaps_array = self.db.get_recording_intervals(net=net_sta.splitsta=net_sta.split('.')[1], chan=chan)
            #
            #     # if station == "7D.CZ40":
            #     #     print("GAPS:")
            #     #     for _i in range(len(gaps_array)):
            #     #
            #     #         print(UTCDateTime(gaps_array[_i, 0]), UTCDateTime(gaps_array[_i, 1]))
            #
            #     self.recording_gaps[station] = gaps_array
            #
            #     temp_start_int = []
            #     temp_end_int = []
            #
            #     gaps_no = gaps_array.shape[1]
            #
            #     prev_endtime = ''
            #
            #     if gaps_no == 0:
            #         temp_start_int.append(rec_start)
            #         temp_end_int.append(rec_end)
            #     else:
            #         # populate the recording intervals dictionary
            #         for _j in range(gaps_no):
            #             gap_start = gaps_array[0, _j]
            #             gap_end = gaps_array[1, _j]
            #
            #             if _j == 0:
            #                 # first interval
            #                 # print(UTCDateTime(rec_start).ctime(), UTCDateTime(gap_entry['gap_start']).ctime())
            #                 temp_start_int.append(rec_start)
            #                 temp_end_int.append(gap_start)
            #                 prev_endtime = gap_end
            #
            #             if _j == gaps_no - 1:
            #                 # last interval
            #                 # print(UTCDateTime(gap_entry['gap_end']).ctime(), UTCDateTime(rec_end).ctime())
            #                 temp_start_int.append(gap_end)
            #                 temp_end_int.append(rec_end)
            #
            #             elif not _j == 0 and not _j == gaps_no - 1:
            #                 # print(UTCDateTime(gaps_list[_j-1]['gap_end']).ctime(), UTCDateTime(gap_entry['gap_start']).ctime())
            #                 temp_start_int.append(prev_endtime)
            #                 temp_end_int.append(gap_start)
            #                 prev_endtime = gap_end
            #
            #     # the [1] element of shape is the number of intervals
            #     rec_int_array = np.array([temp_start_int, temp_end_int])
            #     self.recording_intervals[station] = rec_int_array
            #
            #     #
            #     # if station == "7D.CZ40":
            #     #     print("Intervals")
            #     #     for _i in range(rec_int_array.shape[1]):
            #     #         print(UTCDateTime(rec_int_array[0, _i]), UTCDateTime(rec_int_array[1, _i]))
            #
            #
            #
            #     # # add the gaps into the auxillary data
            #     # self.ds.add_auxiliary_data(data_type=data_type, path=gaps_path, data=gaps_array,
            #     #                            parameters={"Description": "2D Numpy array with "
            #     #                                                       "UTCDateTime Timestamps for the start/end "
            #     #                                                       "of a gap interval"})
            #     # self.ds.add_auxiliary_data(data_type=data_type, path=rec_int_path, data=rec_int_array,
            #     #                            parameters={"Description": "2D Numpy array with "
            #     #                                                       "UTCDateTime Timestamps for the start/end "
            #     #                                                       "of a recording interval"})
            #
            # print("")
            # print("\nFinished calculating station recording intervals")
            # # print("Wrote data into ASDF auxillary information" )
            #
            # # self.build_auxillary_tree_view()
            # # print(self.recording_intervals)
            #
            # # print("running data avail")
            #
            #
            # # if there is an earthquake catalogue loaded then plot the arthquakes on the station availabilty plot
            # if hasattr(self, "cat_df"):
            #
            #     self.data_avail_plot = DataAvailPlot(parent=self, net_list=net_list, sta_list=sta_list,
            #                                      chan_list=[chan], tags_list=tags_list,
            #                                      rec_int_dict=self.recording_intervals, cat_avail=True, cat_df=self.cat_df)
            # else:
            #     self.data_avail_plot = DataAvailPlot(parent=self, net_list=net_list, sta_list=sta_list,
            #                                      chan_list=[chan], tags_list=tags_list,
            #                                      rec_int_dict=self.recording_intervals)
            #
            # # connect to the go button in plot
            # self.data_avail_plot.davailui.go_push_button.released.connect(self.intervals_selected)

    def plot_intervals_selected(self):
        ret = self.data_avail_plot.get_roi_data()

        if ret[0] == "view_region":
            self.extract_waveform_frm_ASDF(True, net_list=ret[1],
                                           sta_list=ret[2],
                                           chan_list=ret[3],
                                           tags_list=ret[4],
                                           ph_st=UTCDateTime(ret[5][0]),
                                           ph_et=UTCDateTime(ret[5][1]),
                                           xquake=False)

        elif ret[0] == "xcor_region":
            self.get_xcor_data(ret)

    def get_xcor_data(self, sel_data):
        """
        Method to retrieve necessary data to perform cross-correlations against permanent network data for data QA/QC
        :return:
        """

        xcor_asdf_file = str(QtGui.QFileDialog.getSaveFileName(
            parent=self, caption="Choose Destination for XCOR ASDF file",
            directory=os.path.expanduser("~"), filter="ASDF files (*.h5)"))
        if not xcor_asdf_file:
            return

        # check if the given filename has a .h5 extension
        if '.' in xcor_asdf_file:
            if not xcor_asdf_file.split('.')[1] == 'h5':
                xcor_asdf_file = xcor_asdf_file.split('.')[0] + '.h5'
            else:
                pass
        else:
            xcor_asdf_file = xcor_asdf_file + '.h5'

        print(xcor_asdf_file)

        xcor_asdf_filename = basename(xcor_asdf_file)

        if os.path.exists(xcor_asdf_file):
            os.remove(xcor_asdf_file)

        # output json filename
        xcor_json_out = xcor_asdf_filename.split(".")[0] + ".json"

        # remove if exists
        if os.path.exists(xcor_json_out):
            os.remove(xcor_json_out)

        # open up new ASDF file for the xcorr data
        xcor_ds = pyasdf.ASDFDataSet(xcor_asdf_file)

        # add the asdf filename as key and the dataset into the file accessor
        self.ASDF_accessor[xcor_asdf_filename] = {"ds": xcor_ds}

        print('Retrieving Data for QC-XCOR from array.....')
        print(sel_data[5])

        # set up a unique identifier counter
        uid_counter = 0

        # function to make a number into a 3 digit string with leading zeros
        def make_threedig(a):
            if len(a) == 1:
                return '00' + a
            elif len(a) == 2:
                return '0' + a
            return a

        # go through each selected station:
        for sta in sel_data[2]:
            print('..............')
            print(sta)

            # get the station xml for station
            net_sta = sel_data[1][0] + '.' + sta

            print(net_sta)

            # sta_accessor
            sta_accessor = self.ds.waveforms[net_sta]

            inv = sta_accessor.StationXML

            select_inv = inv.select(channel="*Z")

            print(inv[0][0].latitude)
            print(inv[0][0].longitude)
            print(inv[0][0].elevation)

            st_bef = Stream()

            # query the asdf file for data within the before roi
            query = self.db.queryByTime(sel_data[1], [sta], sel_data[3], sel_data[4], sel_data[5][sta][0][0],
                                        sel_data[5][sta][0][1])

            for matched_info in query.values():
                # print(matched_info["ASDF_tag"])

                # read the data from the ASDF into stream
                temp_tr = sta_accessor[matched_info["ASDF_tag"]][0]

                # trim trace to start and endtime
                temp_tr.trim(starttime=UTCDateTime(sel_data[5][sta][0][0]), endtime=UTCDateTime(sel_data[5][sta][0][1]))

                # append trace to stream
                st_bef += temp_tr

                # free memory
                temp_tr = None

            if st_bef.__nonzero__():
                # filling no data with 0
                st_bef.merge(fill_value=0)
                print('\nTrimming Traces to specified time interval....')
                # st_bef.trim(starttime=UTCDateTime(sel_data[5][sta][0][0]), endtime=UTCDateTime(sel_data[5][sta][0][1]))

            st_aft = Stream()

            # do the same for the after roi
            # query the asdf file for data within the before roi
            query = self.db.queryByTime(sel_data[1], [sta], sel_data[3], sel_data[4], sel_data[5][sta][1][0],
                                        sel_data[5][sta][1][1])

            for matched_info in query.values():
                # print(matched_info["ASDF_tag"])

                # read the data from the ASDF into stream
                temp_tr = sta_accessor[matched_info["ASDF_tag"]][0]

                # trim trace to start and endtime
                temp_tr.trim(starttime=UTCDateTime(sel_data[5][sta][1][0]), endtime=UTCDateTime(sel_data[5][sta][1][1]))

                # append trace to stream
                st_aft += temp_tr

                # free memory
                temp_tr = None

            if st_aft.__nonzero__():
                # filling no data with 0
                st_aft.merge(fill_value=0)
                print('\nTrimming Traces to specified time interval....')
                # st_aft.trim(starttime=UTCDateTime(sel_data[5][sta][1][0]), endtime=UTCDateTime(sel_data[5][sta][1][1]))

            print(st_bef)
            print(st_aft)

            print('Retrieving Data for QC-XCOR from nearest permanent metwork station.....')
            # now retreive data from IRIS
            try:
                client = Client("IRIS")
                ref_inv = client.get_stations(network="AU",
                                              starttime=UTCDateTime(st_bef[0].stats.starttime),
                                              endtime=UTCDateTime(st_aft[0].stats.endtime),
                                              latitude=inv[0][0].latitude,
                                              longitude=inv[0][0].longitude,
                                              maxradius=2,
                                              level='channel')

                print(ref_inv)

                ref_sta_dict = {}
                # go through ref stations and get data for closest station
                for ref_sta_inv in ref_inv[0]:
                    # calculate diff
                    diff = math.sqrt(math.fabs(ref_sta_inv.latitude - inv[0][0].latitude) ** 2 + math.fabs(
                        ref_sta_inv.longitude - inv[0][0].longitude) ** 2)
                    ref_sta_dict[ref_sta_inv.code] = diff

                sorted_ref_sta = sorted(ref_sta_dict.items(), key=lambda x: x[1])
                print(sorted_ref_sta)

                close_ref_inv = ref_inv.select(channel="*Z", station=sorted_ref_sta[0][0])

                print(close_ref_inv)

                # now retreive waveform data from IRIS
                ref_st_bef = client.get_waveforms(network=close_ref_inv[0].code, station=close_ref_inv[0][0].code,
                                                  channel='*Z', location='*',
                                                  starttime=UTCDateTime(sel_data[5][sta][0][0]),
                                                  endtime=UTCDateTime(sel_data[5][sta][0][1]))

                ref_st_aft = client.get_waveforms(network=close_ref_inv[0].code, station=close_ref_inv[0][0].code,
                                                  channel='*Z', location='*',
                                                  starttime=UTCDateTime(sel_data[5][sta][1][0]),
                                                  endtime=UTCDateTime(sel_data[5][sta][1][1]))

                uid_counter += 1
                bef_uid = uid_counter
                bef_sta = ref_st_bef[0].get_id()

                # add data into asdf
                xcor_ds.add_stationxml(close_ref_inv)
                xcor_ds.add_waveforms(ref_st_bef, tag="id" + make_threedig(str(uid_counter)), labels=["region_1"])

                uid_counter += 1
                aft_uid = uid_counter
                aft_sta = ref_st_bef[0].get_id()

                xcor_ds.add_waveforms(ref_st_aft, tag="id" + make_threedig(str(uid_counter)), labels=["region_2"])



            except FDSNException:
                print("no data from IRIS or server is unavailable. Make sure proxy settings are set correctly")
                uid_counter += 1

            # add the data from temporary stations
            for tr in st_bef:
                xcor_ds.add_waveforms(tr, tag="raw_recording",
                                      labels=["region_1", "id" + make_threedig(str(bef_uid)), bef_sta])

            for tr in st_aft:
                xcor_ds.add_waveforms(tr, tag="raw_recording",
                                      labels=["region_2", "id" + make_threedig(str(aft_uid)), aft_sta])

            # add in station xml
            xcor_ds.add_stationxml(select_inv)

    def plot_single_stn_selected(self):
        pass

    def gather_events_checkbox_selected(self):
        pass

    def reset_plot_view(self):
        pass

    def analyse_p_time(self):
        """
        Method to analyse differnece between expected arrival time and actual arrival time for P arrivals
        :return:
        """
        self.ui.sort_drop_down_button.setEnabled(True)
        self.ui.sort_drop_down_button_2.setEnabled(True)
        self.ui.plot_single_stn_button.setEnabled(True)
        self.ui.gather_events_checkbox.setEnabled(True)

        # open up dialog to set limits for p-picked - p-theoretical time residual
        res_dlg = ResidualSetLimit(parent=self)
        if res_dlg.exec_():
            self.res_limits = res_dlg.getValues()
        else:
            self.res_limits = None

        # dictionary to contain pandas merged array for each event
        self.event_df_dict = {}

        # open up the auxillary data
        p_time_aux_events_list = self.ds.auxiliary_data.ArrivalData.list()

        # go throuugh events
        for _i, event_id in enumerate(p_time_aux_events_list):
            print(event_id)
            # open new empty data fram for pick times for event
            temp_df = pd.DataFrame(data=None, columns=["pick_event_id", "sta", "P_pick_time", "P_as_pick_time"])

            # get the stations list
            p_time_stations_list = self.ds.auxiliary_data.ArrivalData[event_id].list()

            print(p_time_stations_list)

            for _j, tr_id in enumerate(p_time_stations_list):
                p_time = self.ds.auxiliary_data.ArrivalData[event_id][tr_id].parameters["P"]
                p_as_time = self.ds.auxiliary_data.ArrivalData[event_id][tr_id].parameters["P_as"]

                print(p_time, p_as_time)
                temp_df.loc[_j] = [event_id, tr_id, p_time, p_as_time]

            dict_query = event_id in self.event_df_dict

            if not dict_query:
                # save the df to the dictionary
                self.event_df_dict[event_id] = temp_df

            elif dict_query:
                # merge the dataframes for same events
                new_df = pd.merge(self.event_df_dict.get(event_id), temp_df, how='outer', on=['sta', 'pick_event_id'])
                self.event_df_dict[event_id] = new_df

        # now concat all dfs
        self.picks_df = pd.concat(self.event_df_dict.values())

        print("+++++++++++")
        print(self.picks_df)

        def calc_diff(x):
            if x['P_pick_time'] == np.nan or x['P_as_pick_time'] == np.nan:
                return (np.nan)

            P_UTC = UTCDateTime(x['P_pick_time']).timestamp
            P_as_UTC = UTCDateTime(x['P_as_pick_time']).timestamp

            time_diff = P_UTC - P_as_UTC

            # check if time diff is outside of desired bounds
            # if so then append Nan so they are removed
            if self.res_limits:
                if not self.res_limits[0] <= time_diff <= self.res_limits[1]:
                    return (pd.Series([np.nan, np.nan, np.nan]))

            return (pd.Series([P_UTC, P_as_UTC, time_diff]))

        # calculate diff between theoretical and observed tt, change P_pick_time and P_as_pick_time to
        # UTCDateTime stamps
        # The function called by apply will return a dataframe
        self.picks_df[['P_pick_time_UTC', 'P_as_pick_time_UTC', 'tt_diff']] = \
            self.picks_df.apply(calc_diff, axis=1)  # passes series object row-wise to function

        self.picks_df.reset_index(drop=True, inplace=True)
        self.picks_df = self.picks_df.drop(['P_pick_time', 'P_as_pick_time'], axis=1)
        # drop any rows that have Nan in the P_as column (i.e. there is no pick for it - probably means
        # data is to noisy to pick)
        self.picks_df.dropna(subset=['P_as_pick_time_UTC'], inplace=True)
        self.picks_df.reset_index(drop=True, inplace=True)

        # Now normalize the tt_diff column to get colors
        max_val = self.picks_df['tt_diff'].max()
        min_val = self.picks_df['tt_diff'].min()

        self.picks_df['col_val'] = (self.picks_df['tt_diff'] - min_val) / (max_val - min_val)

        self.picks_df['time_mid'] = (self.picks_df['P_pick_time_UTC'] - 0.5 * self.picks_df['tt_diff'])

        print('--------')
        print(self.picks_df)

        self.ui.col_grad_w.setEnabled(True)
        col_change = functools.partial(self.sort_method_selected, self.ui.sort_drop_down_button_2,
                                       ('no_sort', 'no_sort'),
                                       True)
        self.ui.col_grad_w.sigGradientChanged.connect(col_change)

        # update min/max labels
        self.ui.max_lb.setText(str("%.2f" % max_val))
        self.ui.min_lb.setText(str("%.2f" % min_val))

        self.sort_method_selected(self.ui.sort_drop_down_button_2, ('no_sort', 'no_sort'), False)

        self.events_to_df()


def launch():
    # Automatically compile all ui files if they have been changed.
    # compile_and_import_ui_files()

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
    proxy_queary = query_yes_no("Input Proxy Settings?")
    # proxy_queary="no"

    if proxy_queary == 'yes':
        print('')
        proxy = raw_input("Proxy:")
        port = raw_input("Proxy Port:")
        try:
            networkProxy = QtNetwork.QNetworkProxy(QtNetwork.QNetworkProxy.HttpProxy, proxy, int(port))
            QtNetwork.QNetworkProxy.setApplicationProxy(networkProxy)
        except ValueError:
            print('No proxy settings supplied..')
            sys.exit()
    launch()
