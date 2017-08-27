# -*- coding: utf-8 -*-
"""
MyMultiPlotWidget.py -  Convenience class--GraphicsView widget displaying a MultiPlotItem
Copyright 2010  Luke Campagnola
Distributed under MIT/X11 license. See license.txt for more infomation.

!!! Customised because the scrollable area is not working !!!
"""
from PyQt4 import QtCore
from pyqtgraph.widgets.GraphicsView import GraphicsView
from pyqtgraph.graphicsItems import MultiPlotItem as MultiPlotItem

__all__ = ['MyMultiPlotWidget']
class MyMultiPlotWidget(GraphicsView):
    """Widget implementing a graphicsView with a single MultiPlotItem inside."""
    def __init__(self, parent=None):
        self.minPlotHeight = 50
        self.noplts = 0
        self.mPlotItem = MultiPlotItem.MultiPlotItem()
        GraphicsView.__init__(self, parent)
        self.enableMouse(False)
        self.setCentralItem(self.mPlotItem)
        ## Explicitly wrap methods from mPlotItem
        #for m in ['setData']:
            #setattr(self, m, getattr(self.mPlotItem, m))
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
                
    def __getattr__(self, attr):  ## implicitly wrap methods from plotItem
        if hasattr(self.mPlotItem, attr):
            m = getattr(self.mPlotItem, attr)
            if hasattr(m, '__call__'):
                return m
        raise AttributeError(attr)

    def setNumberPlots(self, noplts):
        # new method to set the number of plots in the multiplotitem
        # for some reason this is not being picked up in the MyMultiPlotWidget code shipped with pyqtgraph
        self.noplts = noplts
        self.resizeEvent(None)

    def setMinimumPlotHeight(self, min):
        """Set the minimum height for each sub-plot displayed. 
        
        If the total height of all plots is greater than the height of the 
        widget, then a scroll bar will appear to provide access to the entire
        set of plots.
        
        Added in version 0.9.9
        """
        self.minPlotHeight = min
        self.resizeEvent(None)

    def widgetGroupInterface(self):
        return (None, MyMultiPlotWidget.saveState, MyMultiPlotWidget.restoreState)

    def saveState(self):
        return {}
        #return self.plotItem.saveState()
        
    def restoreState(self, state):
        pass
        #return self.plotItem.restoreState(state)

    def close(self):
        self.mPlotItem.close()
        self.mPlotItem = None
        self.setParent(None)
        GraphicsView.close(self)

    def setRange(self, *args, **kwds):
        GraphicsView.setRange(self, *args, **kwds)
        if self.centralWidget is not None:
            r = self.range
            minHeight = self.noplts * self.minPlotHeight
            if r.height() < minHeight:
                r.setHeight(minHeight)
                r.setWidth(r.width() - self.verticalScrollBar().width())
            self.centralWidget.setGeometry(r)

    def resizeEvent(self, ev):
        if self.closed:
            return
        if self.autoPixelRange:
            self.range = QtCore.QRectF(0, 0, self.size().width(), self.size().height())
        MyMultiPlotWidget.setRange(self, self.range, padding=0, disableAutoPixel=False)  ## we do this because some subclasses like to redefine setRange in an incompatible way.
        self.updateMatrix()
