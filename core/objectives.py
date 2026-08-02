import random
import logging
from django.utils import timezone
from asgiref.sync import sync_to_async
from django.db.models import Sum

from core.models import (
    DailyObjectiveTemplate, 
    UserDailyObjective, 
    ObjectiveRewardPoolItem,
    UserCard
)

log = logging.getLogger("matchdex.objectives")

@sync_to_async
def get_or_assign_daily_objectives(user):
    today = timezone.now().date()
    
    # Get current objectives for today
    objectives = list(UserDailyObjective.objects.filter(user=user, date=today).select_related('template'))
    
    if len(objectives) < 3:
        # User needs new objectives (or missing some)
        existing_templates = [obj.template.id for obj in objectives]
        
        # Get all templates
        all_templates = list(DailyObjectiveTemplate.objects.exclude(id__in=existing_templates))
        
        if not all_templates:
            return objectives # No templates defined in DB
            
        # Select random templates to fill up to 3
        needed = 3 - len(objectives)
        selected_templates = random.sample(all_templates, min(needed, len(all_templates)))
        
        new_objectives = []
        for template in selected_templates:
            new_obj = UserDailyObjective(user=user, template=template, date=today)
            new_obj.save()
            new_objectives.append(new_obj)
            
        objectives.extend(new_objectives)
        
    return objectives


@sync_to_async
def update_objective_progress(user, objective_type, amount=1):
    """
    Updates the progress for a specific objective type for today.
    Called when a user performs an action (e.g. open_pack, play_match).
    """
    today = timezone.now().date()
    
    # Find incomplete objectives of this type for today
    active_objectives = UserDailyObjective.objects.filter(
        user=user, 
        date=today, 
        template__objective_type=objective_type,
        is_claimed=False
    ).select_related('template')
    
    for obj in active_objectives:
        if obj.progress < obj.template.target_amount:
            obj.progress += amount
            # Cap progress at target amount
            if obj.progress > obj.template.target_amount:
                obj.progress = obj.template.target_amount
            obj.save()


@sync_to_async
def claim_objective_reward(user, objective_id):
    """
    Claims the reward for a completed objective.
    Returns a string describing the reward, or None if invalid.
    """
    try:
        obj = UserDailyObjective.objects.get(id=objective_id, user=user)
    except UserDailyObjective.DoesNotExist:
        return None
        
    if obj.is_claimed or obj.progress < obj.template.target_amount:
        return None # Already claimed or not finished
        
    # Pick a random reward from the pool based on weights
    pool_items = list(ObjectiveRewardPoolItem.objects.all())
    if not pool_items:
        return "No rewards configured in the prize pool."
        
    weights = [item.weight for item in pool_items]
    reward = random.choices(pool_items, weights=weights, k=1)[0]
    
    # Grant reward
    reward_text = ""
    if reward.reward_type == "POINTS":
        user.points += reward.amount
        user.save()
        reward_text = f"**{reward.amount} Points**"
    elif reward.reward_type == "PACK":
        if reward.pack:
            from core.models import UserPack
            up, _ = UserPack.objects.get_or_create(user=user, pack=reward.pack)
            up.stash_count += reward.amount
            up.save()
            reward_text = f"**{reward.amount}x {reward.pack.name} Pack**"
        else:
            reward_text = "Invalid Pack Configuration"
    elif reward.reward_type == "CARD":
        if reward.card:
            new_card = UserCard.objects.create(
                owner=user,
                template=reward.card
            )
            reward_text = f"**{reward.card.name} ({reward.card.rarity})**"
        else:
            reward_text = "Invalid Card Configuration"
            
    # Mark claimed
    obj.is_claimed = True
    obj.save()
    
    return reward_text
