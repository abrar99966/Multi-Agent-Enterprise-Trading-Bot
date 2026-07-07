import asyncio
from unittest.mock import AsyncMock, MagicMock
from backend.app.services.ai_service import ai_service
from backend.app.services.trade_service import trade_service
from backend.app.schemas.trade import TradeApproval, TradeRecommendationCreate
from backend.app.models.database import TradeStatus

async def test_workflow():
    print("Testing DB-backed Workflow...")
    
    # Mock DB Session
    db = AsyncMock()
    
    # Mock Scalar result for get_pending_recommendations
    mock_rec = MagicMock()
    mock_rec.id = 1
    mock_rec.symbol = "RELIANCE"
    mock_rec.status = TradeStatus.PENDING_APPROVAL
    
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_rec]
    db.execute.return_value = mock_result
    
    print("1. Fetching Recommendations...")
    pending = await trade_service.get_pending_recommendations(db)
    assert len(pending) == 1
    assert pending[0].symbol == "RELIANCE"
    
    print("2. Processing Approval...")
    # Mock scalar_one_or_none for process_approval
    db.execute.return_value.scalar_one_or_none.return_value = mock_rec
    
    approval = TradeApproval(approved=True, notes="Looks good")
    success = await trade_service.process_approval(db, 1, approval)
    
    assert success is True
    assert mock_rec.status == TradeStatus.EXECUTED
    db.commit.assert_called()
    
    print("DB-backed Workflow Test Passed!")

if __name__ == "__main__":
    asyncio.run(test_workflow())
