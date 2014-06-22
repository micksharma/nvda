#NVDAObjects/excel.py
#A part of NonVisual Desktop Access (NVDA)
#Copyright (C) 2006-2007 NVDA Contributors <http://www.nvda-project.org/>
#This file is covered by the GNU General Public License.
#See the file COPYING for more details.

from comtypes import COMError
import comtypes.automation
import wx
import time
import re
import uuid
import collections
import oleacc
import ui
from tableUtils import HeaderCellInfo, HeaderCellTracker
import config
import textInfos
import colors
import eventHandler
import api
from logHandler import log
import gui
import winUser
from displayModel import DisplayModelTextInfo
import controlTypes
from . import Window
from .. import NVDAObjectTextInfo
import scriptHandler

xlA1 = 1
xlRC = 2
xlUnderlineStyleNone=-4142

re_absRC=re.compile(r'^R(\d+)C(\d+)(?::R(\d+)C(\d+))?$')

class ExcelBase(Window):
	"""A base that all Excel NVDAObjects inherit from, which contains some useful methods."""

	@staticmethod
	def excelWindowObjectFromWindow(windowHandle):
		try:
			pDispatch=oleacc.AccessibleObjectFromWindow(windowHandle,winUser.OBJID_NATIVEOM,interface=comtypes.automation.IDispatch)
		except (COMError,WindowsError):
			return None
		return comtypes.client.dynamic.Dispatch(pDispatch)

	@staticmethod
	def getCellAddress(cell, external=False,format=xlA1):
		text=cell.Address(False, False, format, external)
		textList=text.split(':')
		if len(textList)==2:
			# Translators: Used to express an address range in excel.
			text=_("{start} through {end}").format(start=textList[0], end=textList[1])
		return text

	def _getDropdown(self):
		w=winUser.getAncestor(self.windowHandle,winUser.GA_ROOT)
		if not w:
			log.debugWarning("Could not get ancestor window (GA_ROOT)")
			return
		obj=Window(windowHandle=w,chooseBestAPI=False)
		if not obj:
			log.debugWarning("Could not instnaciate NVDAObject for ancestor window")
			return
		threadID=obj.windowThreadID
		while not eventHandler.isPendingEvents("gainFocus"):
			obj=obj.previous
			if not obj or not isinstance(obj,Window) or obj.windowThreadID!=threadID:
				log.debugWarning("Could not locate dropdown list in previous objects")
				return
			if obj.windowClassName=='EXCEL:':
				break
		return obj

	def _getSelection(self):
		selection=self.excelWindowObject.Selection
		try:
			isMerged=selection.mergeCells
		except (COMError,NameError):
			isMerged=False
		if isMerged:
			obj=ExcelMergedCell(windowHandle=self.windowHandle,excelWindowObject=self.excelWindowObject,excelCellObject=selection.item(1))
		elif selection.Count>1:
			obj=ExcelSelection(windowHandle=self.windowHandle,excelWindowObject=self.excelWindowObject,excelRangeObject=selection)
		else:
			obj=ExcelCell(windowHandle=self.windowHandle,excelWindowObject=self.excelWindowObject,excelCellObject=selection)
		return obj

class Excel7Window(ExcelBase):
	"""An overlay class for Window for the EXCEL7 window class, which simply bounces focus to the active excel cell."""

	def _get_excelWindowObject(self):
		return self.excelWindowObjectFromWindow(self.windowHandle)

	def event_gainFocus(self):
		selection=self._getSelection()
		dropdown=self._getDropdown()
		if dropdown:
			if selection:
				dropdown.parent=selection
			eventHandler.executeEvent('gainFocus',dropdown)
			return
		if selection:
			eventHandler.executeEvent('gainFocus',selection)

class ExcelWorksheet(ExcelBase):

	role=controlTypes.ROLE_TABLE

	def _get_excelApplicationObject(self):
		self.excelApplicationObject=self.excelWorksheetObject.application
		return self.excelApplicationObject

	re_definedName=re.compile(ur'^((?P<sheet>\w+)!)?(?P<name>\w+)(\.(?P<minAddress>[a-zA-Z]+[0-9]+)?(\.(?P<maxAddress>[a-zA-Z]+[0-9]+)?(\..*)*)?)?$')

	def populateHeaderCellTrackerFromNames(self,headerCellTracker):
		sheetName=self.excelWorksheetObject.name
		for x in self.excelWorksheetObject.parent.names:
			fullName=x.name
			nameMatch=self.re_definedName.match(fullName)
			if not nameMatch:
				continue
			sheet=nameMatch.group('sheet')
			if sheet and sheet!=sheetName:
				continue
			name=nameMatch.group('name').lower()
			isColumnHeader=isRowHeader=False
			if name.startswith('title'):
				isColumnHeader=isRowHeader=True
			elif name.startswith('columntitle'):
				isColumnHeader=True
			elif name.startswith('rowtitle'):
				isRowHeader=True
			else:
				continue
			try:
				headerCell=x.refersToRange
			except COMError:
				continue
			if headerCell.parent.name!=sheetName:
				continue
			minColumnNumber=maxColumnNumber=minRowNumber=maxRowNumber=None
			minAddress=nameMatch.group('minAddress')
			if minAddress:
				try:
					minCell=self.excelWorksheetObject.range(minAddress)
				except COMError:
					minCell=None
				if minCell:
					minRowNumber=minCell.row
					minColumnNumber=minCell.column
			maxAddress=nameMatch.group('maxAddress')
			if maxAddress:
				try:
					maxCell=self.excelWorksheetObject.range(maxAddress)
				except COMError:
					maxCell=None
				if maxCell:
					maxRowNumber=maxCell.row
					maxColumnNumber=maxCell.column
			headerCellTracker.addHeaderCellInfo(rowNumber=headerCell.row,columnNumber=headerCell.column,rowSpan=headerCell.rows.count,colSpan=headerCell.columns.count,minRowNumber=minRowNumber,maxRowNumber=maxRowNumber,minColumnNumber=minColumnNumber,maxColumnNumber=maxColumnNumber,name=fullName,isColumnHeader=isColumnHeader,isRowHeader=isRowHeader)

	def _get_headerCellTracker(self):
		self.headerCellTracker=HeaderCellTracker()
		self.populateHeaderCellTrackerFromNames(self.headerCellTracker)
		return self.headerCellTracker

	def setAsHeaderCell(self,cell,isColumnHeader=False,isRowHeader=False):
		oldInfo=self.headerCellTracker.getHeaderCellInfoAt(cell.rowNumber,cell.columnNumber)
		if oldInfo:
			if isColumnHeader and not oldInfo.isColumnHeader:
				oldInfo.isColumnHeader=True
				oldInfo.rowSpan=cell.rowSpan
			elif isRowHeader and not oldInfo.isRowHeader:
				oldInfo.isRowHeader=True
				oldInfo.colSpan=cell.colSpan
			else:
				return False
			isColumnHeader=oldInfo.isColumnHeader
			isRowHeader=oldInfo.isRowHeader
		if isColumnHeader and isRowHeader:
			name="Title_"
		elif isRowHeader:
			name="RowTitle_"
		elif isColumnHeader:
			name="ColumnTitle_"
		else:
			raise ValueError("One or both of isColumnHeader or isRowHeader must be True")
		name+=uuid.uuid4().hex
		if oldInfo:
			self.excelWorksheetObject.parent.names(oldInfo.name).delete()
			oldInfo.name=name
		else:
			self.headerCellTracker.addHeaderCellInfo(rowNumber=cell.rowNumber,columnNumber=cell.columnNumber,rowSpan=cell.rowSpan,colSpan=cell.colSpan,name=name,isColumnHeader=isColumnHeader,isRowHeader=isRowHeader)
		self.excelWorksheetObject.parent.names.add(name,cell.excelRangeObject)
		return True

	def forgetHeaderCell(self,cell,isColumnHeader=False,isRowHeader=False):
		if not isColumnHeader and not isRowHeader: 
			return False
		info=self.headerCellTracker.getHeaderCellInfoAt(cell.rowNumber,cell.columnNumber)
		if not info:
			return False
		if isColumnHeader and info.isColumnHeader:
			info.isColumnHeader=False
		elif isRowHeader and info.isRowHeader:
			info.isRowHeader=False
		else:
			return False
		self.headerCellTracker.removeHeaderCellInfo(info)
		self.excelWorksheetObject.parent.names(info.name).delete()
		if info.isColumnHeader or info.isRowHeader:
			self.setAsHeaderCell(cell,isColumnHeader=info.isColumnHeader,isRowHeader=info.isRowHeader)
		return True

	def fetchAssociatedHeaderCellText(self,cell,columnHeader=False):
		cellRegion=cell.excelCellObject.currentRegion
		if cellRegion.count==1:
			minRow=maxRow=minColumn=maxColumn=None
		else:
			rc=cellRegion.address(True,True,xlRC,False)
			g=[int(x) for x in re_absRC.match(rc).groups()]
			minRow,maxRow,minColumn,maxColumn=min(g[0],g[2]),max(g[0],g[2]),min(g[1],g[3]),max(g[1],g[3])
		for info in self.headerCellTracker.iterPossibleHeaderCellInfosFor(cell.rowNumber,cell.columnNumber,minRowNumber=minRow,maxRowNumber=maxRow,minColumnNumber=minColumn,maxColumnNumber=maxColumn,columnHeader=columnHeader):
			textList=[]
			if columnHeader:
				for headerRowNumber in xrange(info.rowNumber,info.rowNumber+info.rowSpan): 
					headerCell=self.excelWorksheetObject.cells(headerRowNumber,cell.columnNumber)
					textList.append(headerCell.text)
			else:
				for headerColumnNumber in xrange(info.columnNumber,info.columnNumber+info.colSpan): 
					headerCell=self.excelWorksheetObject.cells(cell.rowNumber,headerColumnNumber)
					textList.append(headerCell.text)
			text=" ".join(textList)
			if text:
				return text

	def __init__(self,windowHandle=None,excelWindowObject=None,excelWorksheetObject=None):
		self.excelWindowObject=excelWindowObject
		self.excelWorksheetObject=excelWorksheetObject
		super(ExcelWorksheet,self).__init__(windowHandle=windowHandle)
		for gesture in self.__changeSelectionGestures:
			self.bindGesture(gesture, "changeSelection")

	def _get_name(self):
		return self.excelWorksheetObject.name

	def _isEqual(self, other):
		if not super(ExcelWorksheet, self)._isEqual(other):
			return False
		return self.excelWorksheetObject.index == other.excelWorksheetObject.index

	def _get_firstChild(self):
		cell=self.excelWorksheetObject.cells(1,1)
		return ExcelCell(windowHandle=self.windowHandle,excelWindowObject=self.excelWindowObject,excelCellObject=cell)

	def script_changeSelection(self,gesture):
		oldSelection=api.getFocusObject()
		gesture.send()
		import eventHandler
		import time
		newSelection=None
		curTime=startTime=time.time()
		while (curTime-startTime)<=0.15:
			if scriptHandler.isScriptWaiting():
				# Prevent lag if keys are pressed rapidly
				return
			if eventHandler.isPendingEvents('gainFocus'):
				return
			newSelection=self._getSelection()
			if newSelection and newSelection!=oldSelection:
				break
			api.processPendingEvents(processEventQueue=False)
			time.sleep(0.015)
			curTime=time.time()
		if newSelection:
			if oldSelection.parent==newSelection.parent:
				newSelection.parent=oldSelection.parent
			eventHandler.executeEvent('gainFocus',newSelection)
	script_changeSelection.canPropagate=True

	__changeSelectionGestures = (
		"kb:tab",
		"kb:shift+tab",
		"kb:upArrow",
		"kb:downArrow",
		"kb:leftArrow",
		"kb:rightArrow",
		"kb:control+upArrow",
		"kb:control+downArrow",
		"kb:control+leftArrow",
		"kb:control+rightArrow",
		"kb:home",
		"kb:end",
		"kb:control+home",
		"kb:control+end",
		"kb:shift+upArrow",
		"kb:shift+downArrow",
		"kb:shift+leftArrow",
		"kb:shift+rightArrow",
		"kb:shift+control+upArrow",
		"kb:shift+control+downArrow",
		"kb:shift+control+leftArrow",
		"kb:shift+control+rightArrow",
		"kb:shift+home",
		"kb:shift+end",
		"kb:shift+control+home",
		"kb:shift+control+end",
		"kb:shift+space",
		"kb:control+space",
		"kb:pageUp",
		"kb:pageDown",
		"kb:shift+pageUp",
		"kb:shift+pageDown",
		"kb:alt+pageUp",
		"kb:alt+pageDown",
		"kb:alt+shift+pageUp",
		"kb:alt+shift+pageDown",
		"kb:control+shift+8",
		"kb:control+pageUp",
		"kb:control+pageDown",
		"kb:control+a",
		"kb:control+v",
	)

class ExcelCellTextInfo(NVDAObjectTextInfo):

	def _getFormatFieldAndOffsets(self,offset,formatConfig,calculateOffsets=True):
		formatField=textInfos.FormatField()
		fontObj=self.obj.excelCellObject.font
		if formatConfig['reportFontName']:
			formatField['font-name']=fontObj.name
		if formatConfig['reportFontSize']:
			formatField['font-size']=str(fontObj.size)
		if formatConfig['reportFontAttributes']:
			formatField['bold']=fontObj.bold
			formatField['italic']=fontObj.italic
			underline=fontObj.underline
			formatField['underline']=False if underline is None or underline==xlUnderlineStyleNone else True
		if formatConfig['reportColor']:
			try:
				formatField['color']=colors.RGB.fromCOLORREF(int(fontObj.color))
			except COMError:
				pass
			try:
				formatField['background-color']=colors.RGB.fromCOLORREF(int(self.obj.excelCellObject.interior.color))
			except COMError:
				pass
		return formatField,(self._startOffset,self._endOffset)

class ExcelCell(ExcelBase):

	def _get_columnHeaderText(self):
		return self.parent.fetchAssociatedHeaderCellText(self,columnHeader=True)

	def _get_rowHeaderText(self):
		return self.parent.fetchAssociatedHeaderCellText(self,columnHeader=False)

	def script_openDropdown(self,gesture):
		gesture.send()
		d=None
		curTime=startTime=time.time()
		while (curTime-startTime)<=0.25:
			if scriptHandler.isScriptWaiting():
				# Prevent lag if keys are pressed rapidly
				return
			if eventHandler.isPendingEvents('gainFocus'):
				return
			d=self._getDropdown()
			if d:
				break
			api.processPendingEvents(processEventQueue=False)
			time.sleep(0.025)
			curTime=time.time()
		if not d:
			log.debugWarning("Failed to get dropDown, giving up")
			return
		d.parent=self
		eventHandler.queueEvent("gainFocus",d)

	def script_setColumnHeader(self,gesture):
		scriptCount=scriptHandler.getLastScriptRepeatCount()
		if not config.conf['documentFormatting']['reportTableHeaders']:
			# Translators: a message reported in the SetColumnHeader script for Excel.
			ui.message(_("Cannot set headers. Please enable reporting of table headers in Document Formatting Settings"))
			return
		if scriptCount==0:
			if self.parent.setAsHeaderCell(self,isColumnHeader=True,isRowHeader=False):
				# Translators: a message reported in the SetColumnHeader script for Excel.
				ui.message(_("Set {address} as start of column headers").format(address=self.cellCoordsText))
			else:
				# Translators: a message reported in the SetColumnHeader script for Excel.
				ui.message(_("Already set {address} as start of column headers").format(address=self.cellCoordsText))
		elif scriptCount==1:
			if self.parent.forgetHeaderCell(self,isColumnHeader=True,isRowHeader=False):
				# Translators: a message reported in the SetColumnHeader script for Excel.
				ui.message(_("removed {address}    from column headers").format(address=self.cellCoordsText))
			else:
				# Translators: a message reported in the SetColumnHeader script for Excel.
				ui.message(_("Cannot find {address}    in column headers").format(address=self.cellCoordsText))
	script_setColumnHeader.__doc__=_("Pressing once will set this cell as the first column header for any cells lower and to the right of it within this region. Pressing twice will forget the current column header for this cell.")

	def script_setRowHeader(self,gesture):
		scriptCount=scriptHandler.getLastScriptRepeatCount()
		if not config.conf['documentFormatting']['reportTableHeaders']:
			# Translators: a message reported in the SetRowHeader script for Excel.
			ui.message(_("Cannot set headers. Please enable reporting of table headers in Document Formatting Settings"))
			return
		if scriptCount==0:
			if self.parent.setAsHeaderCell(self,isColumnHeader=False,isRowHeader=True):
				# Translators: a message reported in the SetRowHeader script for Excel.
				ui.message(_("Set {address} as start of row headers").format(address=self.cellCoordsText))
			else:
				# Translators: a message reported in the SetRowHeader script for Excel.
				ui.message(_("Already set {address} as start of row headers").format(address=self.cellCoordsText))
		elif scriptCount==1:
			if self.parent.forgetHeaderCell(self,isColumnHeader=False,isRowHeader=True):
				# Translators: a message reported in the SetRowHeader script for Excel.
				ui.message(_("removed {address}    from row headers").format(address=self.cellCoordsText))
			else:
				# Translators: a message reported in the SetRowHeader script for Excel.
				ui.message(_("Cannot find {address}    in row headers").format(address=self.cellCoordsText))
	script_setRowHeader.__doc__=_("Pressing once will set this cell as the first row header for any cells lower and to the right of it within this region. Pressing twice will forget the current row header for this cell.")

	@classmethod
	def kwargsFromSuper(cls,kwargs,relation=None):
		windowHandle=kwargs['windowHandle']
		excelWindowObject=cls.excelWindowObjectFromWindow(windowHandle)
		if not excelWindowObject:
			return False
		if isinstance(relation,tuple):
			excelCellObject=excelWindowObject.rangeFromPoint(relation[0],relation[1])
		else:
			excelCellObject=excelWindowObject.ActiveCell
		if not excelCellObject:
			return False
		kwargs['excelWindowObject']=excelWindowObject
		kwargs['excelCellObject']=excelCellObject
		return True

	def __init__(self,windowHandle=None,excelWindowObject=None,excelCellObject=None):
		self.excelWindowObject=excelWindowObject
		self.excelCellObject=excelCellObject
		super(ExcelCell,self).__init__(windowHandle=windowHandle)

	def _get_excelRangeObject(self):
		return self.excelCellObject

	def _get_role(self):
		try:
			linkCount=self.excelCellObject.hyperlinks.count
		except (COMError,NameError,AttributeError):
			linkCount=None
		if linkCount:
			return controlTypes.ROLE_LINK
		return controlTypes.ROLE_TABLECELL

	TextInfo=ExcelCellTextInfo

	def _isEqual(self,other):
		if not super(ExcelCell,self)._isEqual(other):
			return False
		thisAddr=self.getCellAddress(self.excelCellObject,True)
		try:
			otherAddr=self.getCellAddress(other.excelCellObject,True)
		except COMError:
			#When cutting and pasting the old selection can become broken
			return False
		return thisAddr==otherAddr

	def _get_cellCoordsText(self):
		return self.getCellAddress(self.excelCellObject)

	def _get__rowAndColumnNumber(self):
		rc=self.excelCellObject.address(True,True,xlRC,False)
		return [int(x) if x else 1 for x in re_absRC.match(rc).groups()]

	def _get_rowNumber(self):
		return self._rowAndColumnNumber[0]

	rowSpan=1

	def _get_columnNumber(self):
		return self._rowAndColumnNumber[1]

	colSpan=1

	def _get_tableID(self):
		address=self.excelCellObject.address(1,1,0,1)
		ID="".join(address.split('!')[:-1])
		ID="%s %s"%(ID,self.windowHandle)
		return ID

	def _get_name(self):
		return self.excelCellObject.Text

	def _get_states(self):
		states=super(ExcelCell,self).states
		if self.excelCellObject.HasFormula:
			states.add(controlTypes.STATE_HASFORMULA)
		try:
			validationType=self.excelCellObject.validation.type
		except (COMError,NameError,AttributeError):
			validationType=None
		if validationType==3:
			states.add(controlTypes.STATE_HASPOPUP)
		try:
			comment=self.excelCellObject.comment
		except (COMError,NameError,AttributeError):
			comment=None
		if comment:
			states.add(controlTypes.STATE_HASCOMMENT)
		return states

	def _get_parent(self):
		worksheet=self.excelCellObject.Worksheet
		self.parent=ExcelWorksheet(windowHandle=self.windowHandle,excelWindowObject=self.excelWindowObject,excelWorksheetObject=worksheet)
		return self.parent

	def _get_next(self):
		try:
			next=self.excelCellObject.next
		except COMError:
			next=None
		if next:
			return ExcelCell(windowHandle=self.windowHandle,excelWindowObject=self.excelWindowObject,excelCellObject=next)

	def _get_previous(self):
		try:
			previous=self.excelCellObject.previous
		except COMError:
			previous=None
		if previous:
			return ExcelCell(windowHandle=self.windowHandle,excelWindowObject=self.excelWindowObject,excelCellObject=previous)

	def script_reportComment(self,gesture):
		commentObj=self.excelCellObject.comment
		text=commentObj.text() if commentObj else None
		if text:
			ui.message(text)
		else:
			# Translators: A message in Excel when there is no comment
			ui.message(_("Not on a comment"))
	# Translators: the description  for a script for Excel
	script_reportComment.__doc__=_("Reports the comment on the current cell")

	def script_editComment(self,gesture):
		commentObj=self.excelCellObject.comment
		d = wx.TextEntryDialog(gui.mainFrame, 
			# Translators: Dialog text for 
			_("Editing comment for cell {address}").format(address=self.cellCoordsText),
			# Translators: Title of a dialog edit an Excel comment 
			_("Comment"),
			defaultValue=commentObj.text() if commentObj else u"",
			style=wx.TE_MULTILINE|wx.OK|wx.CANCEL)
		def callback(result):
			if result == wx.ID_OK:
				if commentObj:
					commentObj.text(d.Value)
				else:
					self.excelCellObject.addComment(d.Value)
		gui.runScriptModalDialog(d, callback)

	__gestures = {
		"kb:shift+f2":"editComment",
		"kb:NVDA+shift+c": "setColumnHeader",
		"kb:NVDA+shift+r": "setRowHeader",
		"kb:alt+downArrow":"openDropdown",
		"kb:NVDA+alt+c":"reportComment",
	}

class ExcelSelection(ExcelBase):

	role=controlTypes.ROLE_TABLECELL

	def __init__(self,windowHandle=None,excelWindowObject=None,excelRangeObject=None):
		self.excelWindowObject=excelWindowObject
		self.excelRangeObject=excelRangeObject
		super(ExcelSelection,self).__init__(windowHandle=windowHandle)

	def _get_states(self):
		states=super(ExcelSelection,self).states
		states.add(controlTypes.STATE_SELECTED)
		return states

	def _get_name(self):
		firstCell=self.excelRangeObject.Item(1)
		lastCell=self.excelRangeObject.Item(self.excelRangeObject.Count)
		# Translators: This is presented in Excel to show the current selection, for example 'a1 c3 through a10 c10'
		return _("{firstAddress} {firstContent} through {lastAddress} {lastContent}").format(firstAddress=self.getCellAddress(firstCell),firstContent=firstCell.Text,lastAddress=self.getCellAddress(lastCell),lastContent=lastCell.Text)

	def _get_parent(self):
		worksheet=self.excelRangeObject.Worksheet
		return ExcelWorksheet(windowHandle=self.windowHandle,excelWindowObject=self.excelWindowObject,excelWorksheetObject=worksheet)

	def _get_rowNumber(self):
		return self.excelRangeObject.row

	def _get_rowSpan(self):
		return self.excelRangeObject.rows.count

	def _get_columnNumber(self):
		return self.excelRangeObject.column

	def _get_colSpan(self):
		return self.excelRangeObject.columns.count

	#Its useful for an excel selection to be announced with reportSelection script
	def makeTextInfo(self,position):
		if position==textInfos.POSITION_SELECTION:
			position=textInfos.POSITION_ALL
		return super(ExcelSelection,self).makeTextInfo(position)

class ExcelDropdownItem(Window):

	firstChild=None
	lastChild=None
	children=[]
	role=controlTypes.ROLE_LISTITEM

	def __init__(self,parent=None,name=None,states=None,index=None):
		self.name=name
		self.states=states
		self.parent=parent
		self.index=index
		super(ExcelDropdownItem,self).__init__(windowHandle=parent.windowHandle)

	def _get_previous(self):
		newIndex=self.index-1
		if newIndex>=0:
			return self.parent.children[newIndex]

	def _get_next(self):
		newIndex=self.index+1
		if newIndex<self.parent.childCount:
			return self.parent.children[newIndex]

	def _get_positionInfo(self):
		return {'indexInGroup':self.index+1,'similarItemsInGroup':self.parent.childCount,}

class ExcelDropdown(Window):

	@classmethod
	def kwargsFromSuper(cls,kwargs,relation=None):
		return kwargs

	role=controlTypes.ROLE_LIST
	excelCell=None

	def _get__highlightColors(self):
		background=colors.RGB.fromCOLORREF(winUser.user32.GetSysColor(13))
		foreground=colors.RGB.fromCOLORREF(winUser.user32.GetSysColor(14))
		self._highlightColors=(background,foreground)
		return self._highlightColors

	def _get_children(self):
		children=[]
		index=0
		states=set()
		for item in DisplayModelTextInfo(self,textInfos.POSITION_ALL).getTextWithFields():
			if isinstance(item,textInfos.FieldCommand) and item.command=="formatChange":
				states=set([controlTypes.STATE_SELECTABLE])
				foreground=item.field.get('color',None)
				background=item.field.get('background-color',None)
				if (background,foreground)==self._highlightColors:
					states.add(controlTypes.STATE_SELECTED)
			if isinstance(item,basestring):
				obj=ExcelDropdownItem(parent=self,name=item,states=states,index=index)
				children.append(obj)
				index+=1
		return children

	def _get_childCount(self):
		return len(self.children)

	def _get_firstChild(self):
		return self.children[0]
	def _get_selection(self):
		for child in self.children:
			if controlTypes.STATE_SELECTED in child.states:
				return child

	def script_selectionChange(self,gesture):
		gesture.send()
		newFocus=self.selection or self
		if eventHandler.lastQueuedFocusObject is newFocus: return
		eventHandler.queueEvent("gainFocus",newFocus)
	script_selectionChange.canPropagate=True

	def script_closeDropdown(self,gesture):
		gesture.send()
		eventHandler.queueEvent("gainFocus",self.parent)
	script_closeDropdown.canPropagate=True

	__gestures={
		"kb:downArrow":"selectionChange",
		"kb:upArrow":"selectionChange",
		"kb:leftArrow":"selectionChange",
		"kb:rightArrow":"selectionChange",
		"kb:home":"selectionChange",
		"kb:end":"selectionChange",
		"kb:escape":"closeDropdown",
		"kb:enter":"closeDropdown",
		"kb:space":"closeDropdown",
	}

	def event_gainFocus(self):
		child=self.selection
		if not child and self.childCount>0:
			child=self.children[0]
		if child:
			eventHandler.queueEvent("focusEntered",self)
			eventHandler.queueEvent("gainFocus",child)
		else:
			super(ExcelDropdown,self).event_gainFocus()

class ExcelMergedCell(ExcelCell):

	def _get_cellCoordsText(self):
		return self.getCellAddress(self.excelCellObject.mergeArea)

	def _get_rowSpan(self):
		return self.excelCellObject.mergeArea.rows.count

	def _get_colSpan(self):
		return self.excelCellObject.mergeArea.columns.count
